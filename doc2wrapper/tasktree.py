"""Object model for a WDL task."""
from dataclasses import dataclass

@dataclass
class Argument:
    """A command argument for a WDL task."""
    name: str
    wdl_type: str
    is_array: bool = False
    is_required: bool = False
    default_value: str | None = None
    option_flag: str = ""
    option_has_value: bool = False
    doc: str = ""

