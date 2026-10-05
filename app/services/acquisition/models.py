"""SEC filing metadata and conservative public-availability timestamps."""

from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SecFiling:
    cik: str
    form: str
    accession: str
    filing_date: date
    report_date: date | None
    primary_document: str
    accepted_at: datetime | None = None
    inline_xbrl: bool = False

    @property
    def available_at(self):
        if self.accepted_at is not None:
            return self.accepted_at.astimezone(timezone.utc)
        # A date does not prove start-of-day availability. Admit only after the
        # entire US Eastern filing day has elapsed, including DST transitions.
        return datetime.combine(
            self.filing_date + timedelta(days=1), time.min, ZoneInfo("America/New_York")
        ).astimezone(timezone.utc)

    @property
    def availability_granularity(self):
        return "timestamp" if self.accepted_at else "date"

    @property
    def fiscal_year(self):
        # Discovery hint only; exact fiscal year comes from the parsed filing.
        if not self.report_date:
            return None
        return self.report_date.year - int(
            self.report_date.month == 1 and self.report_date.day <= 7
        )

    def metadata(self):
        return {
            k: v.isoformat() if isinstance(v, (date, datetime)) else v
            for k, v in asdict(self).items()
        }
