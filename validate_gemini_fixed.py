#!/usr/bin/env python3
# Fixed Gemini validation using google.genai
import os
import sys
import time
from google import genai

def validate_gemini_key_fixed(api_key: str) -> bool:
    """Fixed validation for Google Gemini API Key"""
    try:
        time.sleep(0.5)
        
        # Use correct endpoint and new genai package
        genai.configure(
            api_key=api_key,
            api_endpoint="https://generativelanguage.googleapis.com"
        )
        
        # Test with minimal model list request
        models = genai.list_models()
        for model in models:
            if 'gemini' in model.name.lower():
                print(f"✅ Gemini key valid: {model.name}")
                return True
        
        print("❌ Gemini key returned no gemini models")
        return False
        
    except Exception as e:
        print(f"❌ Gemini validation failed: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_gemini_fixed.py <key>")
        sys.exit(1)
    
    key = sys.argv[1]
    print(f"Testing Gemini key: {key[:10]}...")
    validate_gemini_key_fixed(key)
