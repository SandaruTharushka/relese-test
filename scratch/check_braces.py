import sys
import os

path = r'c:\Projects\garage-pos-fixed (1)\templates\billing.html'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

stack = []
for i, char in enumerate(content):
    if char == '{':
        stack.append(i)
    elif char == '}':
        if stack:
            stack.pop()
        else:
            print(f"Extra closing brace at index {i}")

if stack:
    print(f"Unclosed braces at indexes: {stack}")
    # Print the line number for the first unclosed brace
    first_idx = stack[0]
    line_num = content[:first_idx].count('\n') + 1
    print(f"First unclosed brace is on line {line_num}")
    
    # Show context
    start = max(0, first_idx - 50)
    end = min(len(content), first_idx + 50)
    print(f"Context: {content[start:end]}")
else:
    print("Braces are balanced")
