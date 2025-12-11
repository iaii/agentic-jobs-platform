#!/usr/bin/env python3
"""
Test Slack Socket Mode Connection
This script tests if your Slack tokens can actually connect to Slack
"""

import asyncio
import os
import sys
import ssl

import certifi


async def test_slack_connection():
    """Test the Slack socket mode connection"""
    from agentic_jobs.config import settings
    
    print("=" * 60)
    print("Testing Slack Socket Mode Connection")
    print("=" * 60)
    print()
    
    # Check if tokens are set
    if not settings.slack_bot_token:
        print("❌ SLACK_BOT_TOKEN is not set")
        print("   Please set your environment variables first.")
        return False
    
    if not settings.slack_app_level_token:
        print("❌ SLACK_APP_LEVEL_TOKEN is not set")
        print("   Please set your environment variables first.")
        return False
    
    print(f"✅ Bot Token: {settings.slack_bot_token[:10]}...{settings.slack_bot_token[-5:]}")
    print(f"✅ App Token: {settings.slack_app_level_token[:10]}...{settings.slack_app_level_token[-5:]}")
    print()
    
    # Test the connection
    try:
        from slack_sdk.web.async_client import AsyncWebClient
        from slack_sdk.socket_mode.aiohttp import SocketModeClient
        
        print("🔄 Testing Slack API connection...")
        
        # Test Bot Token
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        web_client = AsyncWebClient(token=settings.slack_bot_token, ssl=ssl_context)
        auth_response = await web_client.auth_test()
        
        if auth_response["ok"]:
            print(f"✅ Bot Token is valid!")
            print(f"   Team: {auth_response.get('team', 'Unknown')}")
            print(f"   User: {auth_response.get('user', 'Unknown')}")
            print(f"   Bot ID: {auth_response.get('bot_id', 'Unknown')}")
        else:
            print(f"❌ Bot Token validation failed: {auth_response.get('error', 'Unknown error')}")
            # Close underlying aiohttp session (AsyncWebClient has no close())
            session = getattr(web_client, "session", None)
            if session is not None and not session.closed:
                await session.close()
            return False
        
        print()
        print("🔄 Testing Socket Mode connection...")
        
        # Test Socket Mode
        socket_client = SocketModeClient(
            app_token=settings.slack_app_level_token,
            web_client=web_client,
        )
        
        # Try to connect (with timeout)
        try:
            await asyncio.wait_for(socket_client.connect(), timeout=10.0)
            print("✅ Socket Mode connection successful!")
            print("   Your app can now receive Slack events in real-time!")
            
            # Disconnect
            await socket_client.close()
            session = getattr(web_client, "session", None)
            if session is not None and not session.closed:
                await session.close()
            
            print()
            print("=" * 60)
            print("🎉 All tests passed!")
            print("=" * 60)
            print()
            print("Your Slack integration is ready to use!")
            print("Start the server with: ./start_server.sh")
            print()
            return True
            
        except asyncio.TimeoutError:
            print("⚠️  Socket Mode connection timed out")
            print("   This might be a network issue or invalid app-level token.")
            await web_client.close()
            return False
        
    except Exception as e:
        print(f"❌ Error testing Slack connection: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    try:
        result = asyncio.run(test_slack_connection())
        return 0 if result else 1
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

