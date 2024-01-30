from enum import Enum

# decorator providing Enum.get() function
def get(cls):
    def getter(name_value: str):
        if isinstance(name_value, cls):
            return name_value

        if isinstance(name_value, str):
            if name_value in [e.name for e in cls]:
                return getattr(cls, name_value)
            if name_value in [e.value for e in cls]:
                return cls(name_value)
            raise ValueError(f"The provided input should be a string identifying a known {cls.__module__}.{cls.__name__}.\nUnderstood values: {[e.value for e in cls]}.\nGot: {name_value}")
        else:
            raise ValueError(f"The provided input should be a string identifying a known {cls.__module__}.{cls.__name__}.\nUnderstood values: {[e.value for e in cls]}.\nGot: {name_value}")

    setattr(cls, 'get', getter)
    return cls


class AutoNameSpace(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.strip("_").replace("__", "-").replace("_", " ")

class AutoNameDash(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower().strip("_").replace("_", "-")