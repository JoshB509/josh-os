"""Update only Sam and Max. Existing other bots and owner settings stay untouched.
No tokens are displayed. Blank input keeps existing values; cancel saves nothing.
"""
import sys
from discord_setup import main

if __name__ == "__main__":
    sys.exit(main(agents=("sam", "max"), keep_owner=True))
