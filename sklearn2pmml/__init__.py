from sklearn.base import BaseEstimator
from sklearn.exceptions import NotFittedError
try:
	from sklearn.compose import ColumnTransformer
except ImportError:
	class ColumnTransformer:
		pass
try:
	from sklearn.feature_selection._base import SelectorMixin
except ImportError:
	from sklearn.feature_selection.base import SelectorMixin
from sklearn.feature_selection import SelectFromModel
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn_pandas import DataFrameMapper
from sklearn2pmml.resources import _package_classpath
from subprocess import PIPE, Popen
from zipfile import ZipFile

import joblib
import numpy
import os
import pandas
import platform
import re
import sklearn
import sklearn_pandas
import tempfile

from .metadata import __copyright__, __license__, __version__
from .pipeline import PMMLPipeline

def _is_categorical(dtype):
	if dtype == object or dtype == str or dtype == bool:
		return True
	elif hasattr(dtype, "name") and dtype.name == "category":
		return True
	return False

class EstimatorProxy(BaseEstimator):

	def __init__(self, estimator, attr_names = ["feature_importances_"]):
		self.estimator = estimator
		self.attr_names = attr_names
		try:
			self._copy_attrs()
		except NotFittedError:
			pass

	def _copy_attrs(self):
		for attr_name in self.attr_names:
			if hasattr(self.estimator, attr_name):
				setattr(self, attr_name, getattr(self.estimator, attr_name))

	def fit(self, X, y = None, **fit_params):
		self.estimator.fit(X, y, **fit_params)
		self._copy_attrs()
		return self

	def predict(self, X, **predict_params):
		return self.estimator.predict(X, **predict_params)

	def predict_proba(self, X, **predict_proba_params):
		return self.estimator.predict_proba(X, **predict_proba_params)

class SelectorProxy(BaseEstimator):

	def __init__(self, selector):
		self.selector = selector
		try:
			self._copy_attrs()
		except NotFittedError:
			pass

	def _copy_attrs(self):
		try:
			setattr(self, "support_mask_", self.selector._get_support_mask())
		except ValueError:
			pass

	def fit(self, X, y = None, **fit_params):
		self.selector.fit(X, y, **fit_params)
		self._copy_attrs()
		return self

	def transform(self, X):
		return self.selector.transform(X)

def _get_steps(obj):
	if isinstance(obj, Pipeline):
		return obj.steps
	elif isinstance(obj, BaseEstimator):
		return [("estimator", obj)]
	else:
		raise ValueError()

def _filter(obj):
	if isinstance(obj, DataFrameMapper):
		obj.features = _filter_steps(obj.features)
		if hasattr(obj, "built_features"):
			if obj.built_features is not None:
				obj.built_features = _filter_steps(obj.built_features)
	elif isinstance(obj, ColumnTransformer):
		obj.transformers = _filter_steps(obj.transformers)
		obj.remainder = _filter(obj.remainder)
		if hasattr(obj, "transformers_"):
			obj.transformers_ = _filter_steps(obj.transformers_)
	elif isinstance(obj, FeatureUnion):
		obj.transformer_list = _filter_steps(obj.transformer_list)
	elif isinstance(obj, Pipeline):
		obj.steps = _filter_steps(obj.steps)
	elif isinstance(obj, SelectorMixin):
		return SelectorProxy(obj)
	elif isinstance(obj, list):
		return [_filter(e) for e in obj]
	return obj

def _filter_steps(steps):
	return [(step[:1] + (_filter(step[1]), ) + step[2:]) for step in steps]

def make_pmml_pipeline(obj, active_fields = None, target_fields = None):
	"""Translates a regular Scikit-Learn estimator or pipeline to a PMML pipeline.

	Parameters:
	----------
	obj: BaseEstimator
		The object.

	active_fields: list of strings, optional
		Feature names. If missing, "x1", "x2", .., "xn" are assumed.

	target_fields: list of strings, optional
		Label name(s). If missing, "y" is assumed.

	"""
	steps = _filter_steps(_get_steps(obj))
	pipeline = PMMLPipeline(steps)
	if active_fields is not None:
		pipeline.active_fields = numpy.asarray(active_fields)
	if target_fields is not None:
		pipeline.target_fields = numpy.asarray(target_fields)
	return pipeline

def _decode(data, encoding):
	try:
		return data.decode(encoding, errors = "ignore")
	except ValueError:
		return ""

def _java_version(java_encoding, java_home = ""):
	try:
		process = Popen([java_home + "java", "-version"], stdout = PIPE, stderr = PIPE, bufsize = 1)
	except:
		return None
	output, error = process.communicate()
	retcode = process.poll()
	if retcode:
		return None
	match = re.match("^(.*)\sversion\s\"(.*)\"(|\s\d\d\d\d\-\d\d\-\d\d)$", _decode(error, java_encoding), re.MULTILINE)
	if match:
		return (match.group(1), match.group(2))
	else:
		return None

def _classpath(user_classpath):
	return _package_classpath() + user_classpath

def _process_classpath(name, fun, user_classpath):
	jars = _classpath(user_classpath)
	for jar in jars:
		with ZipFile(jar, "r") as zipfile:
			try:
				zipentry = zipfile.getinfo(name)
			except KeyError:
				pass
			else:
				fun(zipfile.open(zipentry))

def _dump(obj, prefix):
	fd, path = tempfile.mkstemp(prefix = (prefix + "-"), suffix = ".pkl.z")
	try:
		joblib.dump(obj, path, compress = 3)
	finally:
		os.close(fd)
	return path

def sklearn2pmml(pipeline, pmml, user_classpath = [], with_repr = False, debug = False, java_encoding = "UTF-8", java_home = ""):
	"""Converts a fitted PMML pipeline object to PMML file.

	Parameters:
	----------
	pipeline: PMMLPipeline
		The input PMML pipeline object.

	pmml: string
		The output PMML file.

	user_classpath: list of strings, optional
		The paths to JAR files that provide custom Transformer, Selector and/or Estimator converter classes.
		The JPMML-SkLearn classpath is constructed by appending user JAR files to package JAR files.

	with_repr: boolean, optional
		If true, insert the string representation of pipeline into the PMML document.

	debug: boolean, optional
		If true, print information about the conversion process.

	java_encoding: string, optional
		The character encoding to use for decoding Java output and error byte streams.

	"""
	if debug:
		java_version = _java_version(java_encoding, java_home)
		if java_version is None:
			java_version = ("java", "N/A")
		print("python: {0}".format(platform.python_version()))
		print("sklearn: {0}".format(sklearn.__version__))
		print("sklearn2pmml: {0}".format(__version__))
		print("joblib: {0}".format(joblib.__version__))
		print("sklearn_pandas: {0}".format(sklearn_pandas.__version__))
		print("pandas: {0}".format(pandas.__version__))
		print("numpy: {0}".format(numpy.__version__))
		print("{0}: {1}".format(java_version[0], java_version[1]))
	if not isinstance(pipeline, PMMLPipeline):
		raise TypeError("The pipeline object is not an instance of " + PMMLPipeline.__name__ + ". Use the 'sklearn2pmml.make_pmml_pipeline(obj)' utility function to translate a regular Scikit-Learn estimator or pipeline to a PMML pipeline")
	estimator = pipeline._final_estimator
	cmd = [java_home + "java", "-cp", os.pathsep.join(_classpath(user_classpath)), "org.jpmml.sklearn.Main"]
	dumps = []
	try:
		if with_repr:
			pipeline.repr_ = repr(pipeline)
		# if isinstance(estimator, H2OEstimator):
		if hasattr(estimator, "download_mojo"):
			estimator_mojo = estimator.download_mojo()
			dumps.append(estimator_mojo)
			estimator._mojo_path = estimator_mojo
		pipeline_pkl = _dump(pipeline, "pipeline")
		cmd.extend(["--pkl-pipeline-input", pipeline_pkl])
		dumps.append(pipeline_pkl)
		cmd.extend(["--pmml-output", pmml])
		if debug:
			print("Executing command:\n{0}".format(" ".join(cmd)))
		try:
			process = Popen(cmd, stdout = PIPE, stderr = PIPE, bufsize = 1)
		except OSError:
			raise RuntimeError("Java is not installed, or the Java executable is not on system path")
		output, error = process.communicate()
		retcode = process.poll()
		if debug or retcode:
			if(len(output) > 0):
				print("Standard output:\n{0}".format(_decode(output, java_encoding)))
			else:
				print("Standard output is empty")
			if(len(error) > 0):
				print("Standard error:\n{0}".format(_decode(error, java_encoding)))
			else:
				print("Standard error is empty")
		if retcode:
			raise RuntimeError("The JPMML-SkLearn conversion application has failed. The Java executable should have printed more information about the failure into its standard output and/or standard error streams")
	finally:
		if debug:
			print("Preserved joblib dump file(s): {0}".format(" ".join(dumps)))
		else:
			for dump in dumps:
				os.remove(dump)

def _parse_properties(lines):
	splitter = re.compile("\s*=\s*")
	properties = dict()
	for line in lines:
		line = line.decode("UTF-8").rstrip()
		if line.startswith("#"):
			continue
		key, value = splitter.split(line)
		properties[key] = value
	return properties

def _supported_classes(user_classpath):
	classes = []
	parser = lambda x: classes.extend(_parse_properties(x.readlines()).keys())
	_process_classpath("META-INF/sklearn2pmml.properties", parser, user_classpath)
	return classes

def _strip_module(name):
	parts = name.split(".")
	if len(parts) > 1:
		parts.pop(-2)
		return ".".join(parts)
	return name

def make_tpot_pmml_config(config, user_classpath = []):
	"""Translates a regular TPOT configuration to a PMML-compatible TPOT configuration.

	Parameters:
	----------
	obj: config
		The configuration dictionary.

	user_classpath: list of strings, optional
		The paths to JAR files that provide custom Transformer, Selector and/or Estimator converter classes.
		The JPMML-SkLearn classpath is constructed by appending user JAR files to package JAR files.

	"""
	tpot_keys = set(config.keys())
	classes = _supported_classes(user_classpath)
	pmml_keys = (set(classes)).union(set([_strip_module(class_) for class_ in classes]))
	return { key : config[key] for key in (tpot_keys).intersection(pmml_keys)}
