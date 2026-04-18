import sys
import re

def check_file(path):
    print(f"Checking {path}")
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Extract script contents
    scripts = re.findall(r'<script.*?> (.*?) </script>', content, re.DOTALL | re.IGNORECASE)
    for i, s in enumerate(scripts):
        # Check for unclosed backticks
        if s.count('`') % 2 != 0:
            print(f"  [ERROR] Unclosed backtick in script block {i}")
            # Find line number
            start_off = content.find(s)
            snippet = s.split('`')[-1]
            line_no = content[:start_off + s.rfind('`')].count('\n') + 1
            print(f"    Likely near line {line_no}")
        
        # Check for non-ascii characters that might be weird
        for char in s:
            if ord(char) > 127 and char not in '🏪👤🧾🔧💰📊🕒🛒✅ℹ️⚠️🚫❌':
                print(f"  [WARN] Non-ASCII character found: {char} (ord {ord(char)})")

if __name__ == "__main__":
    check_file(r'c:\Projects\garage-pos-fixed (1)\templates\billing.html')
    check_file(r'c:\Projects\garage-pos-fixed (1)\templates\customer_profile.html')
