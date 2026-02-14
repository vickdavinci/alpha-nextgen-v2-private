#!/usr/bin/env python3
import re

# Read the enhanced script
with open("scripts/vass_rca_enhanced.py", "r") as f:
    content = f.read()

# Fix the exit details matching - the key format needs to match exactly
old_match_code = '''    # Match exit details to spreads
    for spread in spreads:
        key = f"{spread['long_symbol']}|{spread['short_symbol']}"
        if key in exit_details:
            spread.update(exit_details[key])
        else:
            spread["reason"] = "UNKNOWN"'''

new_match_code = '''    # Match exit details to spreads
    for spread in spreads:
        # Try exact match first
        key = f"{spread['long_symbol']}|{spread['short_symbol']}"
        if key in exit_details:
            spread.update(exit_details[key])
        else:
            # Try normalized match (remove extra spaces)
            norm_key = "|".join([s.strip() for s in key.split("|")])
            found = False
            for exit_key, details in exit_details.items():
                norm_exit_key = "|".join([s.strip() for s in exit_key.split("|")])
                if norm_key == norm_exit_key:
                    spread.update(details)
                    found = True
                    break
            if not found:
                spread["reason"] = "UNKNOWN"'''

content = content.replace(old_match_code, new_match_code)

# Write back
with open("scripts/vass_rca_enhanced.py", "w") as f:
    f.write(content)

print("Fixed exit matching logic")
