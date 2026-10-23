"""Failures the CLI can explain without a traceback."""


class HeraldError(Exception):
    """Expected failure."""


class AuthRequired(HeraldError):
    """The site wants credentials that are missing."""


class ChallengeDetected(HeraldError):
    """The response is a captcha or bot challenge. Herald does not solve those."""


class RateLimited(HeraldError):
    """The host blocked or throttled this run. Do not retry inside the run."""


class ParseFailure(HeraldError):
    """A successful response did not match the document we understand."""


class ProviderNotImplemented(HeraldError):
    """Credentials may be stored, but this fetch is intentionally not built."""


class AlreadyRunning(HeraldError):
    """Another check process holds the lock."""


class ConfigError(HeraldError):
    """Configuration or a command argument is invalid."""


class CoverUnavailable(HeraldError):
    """A poster URL was known, but the image itself could not be sent."""
