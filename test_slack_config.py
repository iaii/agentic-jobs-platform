#!/usr/bin/env python3
"""
Test Slack Configuration Script
This script checks if your Slack tokens are properly configured
"""

import os
import sys


def check_env_var(name: str, expected_prefix: str = None) -> tuple[bool, str]:
    """Check if an environment variable is set and optionally validate its prefix"""
    value = os.environ.get(name)
    
    if not value:
        return False, f"❌ {name} is not set"
    
    if value.startswith(("xoxb-replace", "xapp-replace", "replace-with", "dummy")):
        return False, f"⚠️  {name} is set but contains a placeholder value"
    
    if expected_prefix and not value.startswith(expected_prefix):
        return False, f"⚠️  {name} is set but doesn't start with expected prefix '{expected_prefix}'"
    
    masked_value = f"{value[:10]}...{value[-5:]}" if len(value) > 15 else "***"
    return True, f"✅ {name} is set: {masked_value}"


def main():
    print("=" * 60)
    print("Slack Configuration Test")
    print("=" * 60)
    print()
    
    checks = [
        ("SLACK_BOT_TOKEN", "xoxb-"),
        ("SLACK_APP_LEVEL_TOKEN", "xapp-"),
        ("SLACK_SIGNING_SECRET", None),
        ("SLACK_JOBS_FEED_CHANNEL", None),
        ("SLACK_JOBS_DRAFTS_CHANNEL", None),
        ("DATABASE_URL", None),
    ]
    
    all_good = True
    warnings = []
    
    for var_name, expected_prefix in checks:
        is_ok, message = check_env_var(var_name, expected_prefix)
        print(message)
        if not is_ok:
            all_good = False
            if "placeholder" in message:
                warnings.append(var_name)
    
    print()
    print("=" * 60)
    
    if all_good:
        print("✅ All environment variables are properly configured!")
        print()
        print("You can now start the server with:")
        print("  ./start_server.sh")
        print()
        print("Or manually with:")
        print("  uvicorn agentic_jobs.main:app --reload --host 0.0.0.0 --port 8000")
        return 0
    else:
        print("❌ Some environment variables need to be configured")
        print()
        
        if warnings:
            print("⚠️  Placeholder values detected for:")
            for var in warnings:
                print(f"   - {var}")
            print()
        
        print("📝 To fix this, set your actual Slack tokens:")
        print()
        print("export SLACK_BOT_TOKEN='xoxb-your-actual-token'")
        print("export SLACK_APP_LEVEL_TOKEN='xapp-your-actual-token'")
        print("export SLACK_SIGNING_SECRET='your-actual-secret'")
        print()
        print("Get your tokens from: https://api.slack.com/apps")
        print("Then run this test script again to verify.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

