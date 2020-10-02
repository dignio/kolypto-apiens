from __future__ import annotations

import functools

import inspect
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Type, Any, Union

from apiens import di, errors
from apiens.operations import operation
from apiens.errors import BaseApplicationError
from apiens.util import decomarker


class doc(decomarker):
    """ Document an operation

    Usage:
        from apiens import operation, doc, di

        @operation()
        @doc.function('...', '...')
        @doc.parameter('id', '...', '...')
        @doc.result('...', '...')
        @doc.error(E_NOT_FOUND, '...', '...')
        @di.signature('ssn')
        def get_user(id: int, ssn: Session):
            ...
    """

    function_doc: Optional[FunctionDoc]
    parameters_doc: Dict[str, ParameterDoc]
    result_doc: Optional[ResultDoc]
    errors_doc: Dict[Type[BaseApplicationError], ErrorDoc]
    deprecated_doc: Optional[DeprecatedDoc]

    def __init__(self):
        """ Provide documentation for the function """
        super().__init__()
        self.function_doc = None
        self.result_doc = None
        self.parameters_doc = {}
        self.errors_doc = {}
        self.deprecated_doc = None

        # Flag, enabled by @doc.string()
        self._parse_docstring = False

    @classmethod
    def string(cls):
        """ Use @doc.string() to extract the function's documentation from its docstring

        It parses docstrings in the Google format: https://google.github.io/styleguide/pyguide.html#383-functions-and-methods
        The following sections are parsed:

        * [docstring body]
        * Args / Arguments / Params / Parameters
        * Returns / Yields
        * Raises / Except / Exceptions
        """
        marker = cls()
        marker._parse_docstring = True
        return marker

    @classmethod
    def function(cls, summary: str, description: str = None):
        """ Document the function itself, what it does

        Args:
            summary: Short summary text
            description: Longer description
        """
        marker = cls()
        marker.function_doc = FunctionDoc(summary=summary, description=description)
        return marker

    @classmethod
    def parameter(cls, name: str, summary: str, description: str = None):
        """ Document a function's parameter

        Args:
            name: The parameter name
            summary: Short summary text
            description: Longer description
        """
        marker = cls()
        marker.parameters_doc[name] = ParameterDoc(name=name, summary=summary, description=description)
        return marker

    @classmethod
    def result(cls, summary: str, description: str = None):
        """ Document the function's return value

        Args:
            summary: Short summary text
            description: Longer description
        """
        marker = cls()
        marker.result_doc = ResultDoc(summary=summary, description=description)
        return marker

    @classmethod
    def error(cls, error: Type[BaseApplicationError], summary: str, description: str = None):
        """ Document an expected error that may be returned by this fucntion

        Args:
            error: A subclass of BaseApplicationError that implements some error
            summary: Short summary text
            description: Longer description
        """
        marker = cls()
        marker.result_doc = ErrorDoc(error=error, summary=summary, description=description)
        return marker

    def decorator(self, func: Callable):
        # Parse the function's docstring, if asked to
        if self._parse_docstring:
            try:
                self._parse_from_function_docstring(func)
            except Exception as e:
                raise ValueError(f'Error while parsing docstring of {func}') from e

        # If the function is decorated multiple times, merge
        marker = self.get_from(func)
        if marker is not None:
            self.merge(marker)

        # Done
        return super().decorator(func)

    def validate(self):
        """ Check that all documentations make sense """
        # Check that every documented parameter is actually a known parameter
        documented_parameter_names = set(self.parameters_doc)
        function_parameter_names = set(inspect.signature(self.func).parameters)
        mistaken_parameter_names = documented_parameter_names - function_parameter_names
        assert not mistaken_parameter_names, (
            f'Unknown names given to @doc.parameter(): {mistaken_parameter_names}. '
            f'This is probly a typo. Please document this function properly: {self.func}.'
        )

    def assert_is_fully_documented(self):
        """ Check that the function is fully documented. Used in projects by documenting freaks.

        It will test that:

        * The function is documented
        * Every parameter and the return value are both annotated and documented

        Raises:
            AssertionError if anything failed.
        """
        errors = []

        # Check that the function itself is documented
        if not self.function_doc:
            errors.append(f"The function is not documented. Please add a docstring, or use `@doc.function()`.")

        # Check that its return value is documented
        if not self.result_doc:
            errors.append(f"The return value is not documented. Please add the 'Returns' section, or use `@doc.result()`. ")

        # Get its signature
        signature = operation.get_from(self.func).signature

        # Check that its return value is typed
        if signature.return_type is Any:
            errors.append(f"The return type is unknown. Please provide a return type annotation.")

        # Check every parameter's type and documentation
        for name, type_ in signature.arguments.items():
            if type_ is Any:
                errors.append(f"Parameter {name!r} is not documented. "
                              f"Please add an 'Args' section for it, or use `@doc.parameter()`"
                              f"")
            if name not in self.parameters_doc:
                errors.append(f"Parameter {name!r} type is unknown. "
                              f"Please put a type annotation on it.")

        # We can't validate errors.
        # But error_validator_decorator() can make a wrapper that will validate them at runtime :)

        # Report errors
        if errors:
            errors_str = ''.join(f'\t* {error}\n' for error in errors)
            raise AssertionError(
                f'Function is not fully documented: {self.func}\n'
                f'Errors: \n'
                f'{errors_str}'
            )

    def merge(self, another: doc):
        """ Merge the documentation from another marker into this one """
        # Dicts: update()
        self.parameters_doc.update(another.parameters_doc)
        self.errors_doc.update(another.errors_doc)

        # Dataclasses: merge()
        self.function_doc = self.function_doc.merge(another.function_doc) if self.function_doc else another.function_doc
        self.result_doc = self.result_doc.merge(another.result_doc) if self.result_doc else another.result_doc
        self.deprecated_doc = self.deprecated_doc.merge(another.deprecated_doc) if self.deprecated_doc else another.deprecated_doc

    def wrap_func_check_thrown_errors_are_documented(self, func: Callable) -> Callable:
        """ A wrapper that will make sure that every error raised by the function is documented """

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Call the function
            try:
                return func(*args, **kwargs)
            # Catch errors, see if they're documented
            except BaseApplicationError as e:
                if type(e) not in self.errors_doc:
                    raise AssertionError(
                        f"Function {func} raised an undocumented error: {e.name!r}. "
                        f"Please put a @doc.error({e.name!r}, '', '') on the function and document it."
                    ) from e
                raise

        # Done
        return wrapper

    def _parse_from_function_docstring(self, func: Callable):
        """ Parse the function's documentation from its docstring """
        # Optional import of the `docstring_parser` library
        from docstring_parser import (
            DocstringParam,
            DocstringReturns,
            DocstringRaises,
            DocstringDeprecated,
        )
        from docstring_parser.google import parse

        # Parse the docstring
        parsed_docstring = parse(func.__doc__)

        # Parse: title & description
        if parsed_docstring.short_description or parsed_docstring.long_description:
            self._add_doc_for_function(
                summary=parsed_docstring.short_description,
                description=parsed_docstring.long_description or None,
            )

        # Parse: docstring sections
        for section in parsed_docstring.meta:
            # Parse: parameters
            if isinstance(section, DocstringParam):
                summary, _, description = section.description.partition('\n')
                self._add_doc_for_parameter(section.arg_name, summary, description or None)
            # Parse: return value
            elif isinstance(section, DocstringReturns):
                self._add_doc_for_result(section.description, None)
            # Parse: deprecation marker
            elif isinstance(section, DocstringDeprecated):
                self._add_doc_deprecated(section.version, section.description, None)
            # Parse: errors
            elif isinstance(section, DocstringRaises):
                self._add_doc_exception_name(func, section.type_name.strip(), section.description, None)
            # Parse: unknown sections
            else:
                self._add_doc_for_function(summary='', description=''.join(section.args) + ':\n' + section.description)

    def _add_doc_for_function(self, summary: str, description: Optional[str]):
        """ Add function documentation """
        if not self.function_doc:
            self.function_doc = FunctionDoc('', None)
        self.function_doc.merge(FunctionDoc(
            summary,
            description or None
        ))

    def _add_doc_for_parameter(self, name: str, summary: str, description: Optional[str]):
        """ Add documentation for a parameter """
        self.parameters_doc[name] = ParameterDoc(
            name=name,
            summary=summary,
            description=description or None,
        )

    def _add_doc_for_result(self, summary: str, description: Optional[str]):
        """ Add documentation for the return value """
        if not self.result_doc:
            self.result_doc = ResultDoc('', None)
        self.result_doc.merge(ResultDoc(
            summary=summary,
            description=description or None,
        ))

    def _add_doc_deprecated(self, version: str, summary: str, description: Optional[str]):
        """ Add documentation for a deprecation """
        if not self.deprecated_doc:
            self.deprecated_doc = DeprecatedDoc('', None, '')
        self.deprecated_doc.merge(DeprecatedDoc(
            version=version,
            summary=summary,
            description=description or None,
        ))

    def _add_doc_exception(self, error_cls: Type[errors.BaseApplicationError], summary: str, description: Optional[str]):
        """ Add documentation for an exception type """
        self.errors_doc[error_cls] = ErrorDoc(
            error=error_cls,
            summary=summary,
            description=description,
        )

    def _add_doc_exception_name(self, func: Callable, error_name: str, summary: str, description: Optional[str]):
        """ Add documentation for an exception by name """
        # Ignore global error names
        if error_name in __builtins__:
            return

        # Resolve the error type using function's global namespace
        try:
            error_cls = find_object_in_namespace(func.__globals__, error_name)
        except (KeyError, AttributeError) as e:
            raise ValueError(
                f"The docstring for function {func} references an error named {error_name!r}, "
                f"but it cannot be found in the function's globals. Please make sure its available "
                f"either by its name (e.g. `E_FAIL`), or by reference (`exc.E_FAIL`). "
            ) from e

        # Ignore non-API errors. They're technical.
        if not issubclass(error_cls, errors.BaseApplicationError):
            return

        # Document it
        self._add_doc_exception(error_cls, summary, description)


@dataclass
class Documentation:
    # A short, one-line summary
    summary: str

    # A longer description
    description: Optional[str]

    def merge(self, another: Optional[Documentation]) -> Documentation:
        if another:
            self.summary = (self.summary or '') + (another.summary or '')
            self.description = ((self.description or '') + (another.description or '')) or None
        return self


@dataclass
class FunctionDoc(Documentation):
    """ Documentation for a function """


@dataclass
class ParameterDoc(Documentation):
    """ Documentation for a parameter """
    # Parameter name
    name: str


@dataclass
class ResultDoc(Documentation):
    """ Documentation for the return value """


@dataclass
class ErrorDoc(Documentation):
    """ Documentation for a known exception """
    error: Type[BaseApplicationError]


@dataclass
class DeprecatedDoc(Documentation):
    """ Documentation for a function deprecation """
    # When was it deprecated
    version: str


def find_object_in_namespace(namespace: dict, reference: str):
    """ Given a namespace (like globals()), get the object referenced by `reference`

    Example:
        find_object_in_namespace( func.__globals__, 'errors.E_UNEXPECTED_ERROR' )
    """
    # Support "object.name" attribute access
    name, _, attribute = reference.partition('.')

    # Get the object itself
    object = namespace[name]

    # Get the attribute, recursively
    while True:
        attribute, _, next_attribute = attribute.partition('.')
        object = getattr(object, attribute)
        if not next_attribute:
            break

    # Done
    return object