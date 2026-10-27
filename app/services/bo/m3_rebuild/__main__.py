"""Offline default. Paid probes and filing calls require explicit separate gates."""
import argparse
import json
from pathlib import Path
from .workflow import run_rebuild, run_directory


def main(argv=None):
    parser = argparse.ArgumentParser(description="Select the two-component Module 3 replacement")
    parser.add_argument("case_id")
    parser.add_argument("--structure", type=Path)
    parser.add_argument("--filing", type=Path)
    parser.add_argument("--company")
    parser.add_argument("--filing-period")
    parser.add_argument("--extraction", type=Path, help="Saved offline model-shaped extraction, not a live request")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--authorize-paid-filing-test", action="store_true", help="Only after separate user authorization")
    args = parser.parse_args(argv)
    if not args.structure and not args.filing:
        parser.error("Supply at least --structure or --filing")
    if args.filing and (not args.company or not args.filing_period):
        parser.error("--filing requires --company and --filing-period")
    if args.extraction and args.authorize_paid_filing_test:
        parser.error("Choose saved extraction or paid filing extraction")
    extractor = None
    if args.authorize_paid_filing_test:
        from .provider import VertexExtractor
        extractor = VertexExtractor(authorized=True)
    directory = run_directory(args.output, args.case_id)
    manifest = run_rebuild(directory, case_id=args.case_id, structure_input=args.structure,
                           filing_pdf=args.filing, company=args.company, filing_period=args.filing_period,
                           extraction=args.extraction, extractor=extractor)
    print(json.dumps({"directory": str(directory), **manifest}, indent=2))
    return 1 if any(c["status"] == "failed" for c in manifest["components"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
