class SkipApplication(Exception):
    """Raised to skip an application for a specific job."""

class ApplicationFailed(Exception):
    """Raised when an application fails unexpectedly."""
class ReachedLimit(Exception):
    """Raised when the application limit for the day is reached."""
class Terminate(Exception):
    """Raised to terminate the entire application process."""