#!/usr/bin/env python3
"""Daily cleanup job for ToolShop.

Deletes every product created through the API (rows that have an edit token) and
keeps only the baseline catalog that ships with the app. This runs once a day to
keep the database small.

Scheduled on Render as a Cron Job (see render.yaml). Can also be run manually:
    python scripts/cleanup.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as toolshop


def main():
    removed = toolshop.delete_user_products()
    print(f"[cleanup] removed {removed} user-created product(s); baseline catalog kept.")


if __name__ == "__main__":
    main()
