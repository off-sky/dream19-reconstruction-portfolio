#!/usr/bin/env python3
"""Run the UNMODIFIED VIIRS comparison with explicit QA sensitivity settings.

The underlying compare_information_models.py uses module constants for QA.
This wrapper merely makes those constants CLI-adjustable.  It does not
alter equations, inputs, selected zones, routing or TomTom comparisons.
The initial protocol is 5 valid days and 8 paired months; all others post hoc.
"""
import argparse
import sys
import compare_information_models as original

def main():
    ap=argparse.ArgumentParser(description=__doc__,add_help=False)
    ap.add_argument('--qa-min-days',type=float,default=5.0)
    ap.add_argument('--qa-min-months',type=int,default=8)
    args,rest=ap.parse_known_args()
    if not 0 < args.qa_min_days<=31 or not 1<=args.qa_min_months<=12:
        ap.error('Invalid VIIRS QA thresholds')
    original.MIN_VALID_DAYS=(int(args.qa_min_days) if args.qa_min_days.is_integer() else args.qa_min_days)
    original.MIN_MONTHS=args.qa_min_months
    sys.argv=[sys.argv[0]]+rest
    original.main()

if __name__=='__main__':main()
