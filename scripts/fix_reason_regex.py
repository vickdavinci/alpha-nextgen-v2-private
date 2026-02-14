#!/usr/bin/env python3
import re

with open("scripts/vass_rca_enhanced.py", "r") as f:
    content = f.read()

# The issue is the reason extraction - it's grabbing the first word after pipe, which is EXIT_SIGNAL
# We need to grab the actual reason which comes after the second pipe

old_reason = '''                # Extract exit reason (first word after pipe)
                reason_match = re.search(r'\|\s*(\w+)', line)
                reason = reason_match.group(1) if reason_match else "UNKNOWN"'''

new_reason = '''                # Extract exit reason (word after second pipe, before percentage or paren)
                reason_match = re.search(r'Key=[^|]+\|\s*(\w+)', line)
                reason = reason_match.group(1) if reason_match else "UNKNOWN"'''

content = content.replace(old_reason, new_reason)

with open("scripts/vass_rca_enhanced.py", "w") as f:
    f.write(content)

print("Fixed reason extraction regex")
