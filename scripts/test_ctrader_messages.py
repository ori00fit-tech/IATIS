"""
scripts/test_ctrader_messages.py
---------------------------------
Diagnostic script to reveal the ACTUAL message structure from ctrader-open-api.

This script:
1. Connects to cTrader Open API
2. Prints EVERY message received (unfiltered)
3. Shows message type, attributes, and structure
4. Helps identify the correct API for message handling

Run: python scripts/test_ctrader_messages.py
"""
import os
import threading
import time
from typing import Any

# Setup logging
from utils.logger import get_logger
logger = get_logger(__name__)


class CTraderDiagnostic:
    """Minimal cTrader client for message inspection."""

    DEMO_HOST = "demo.ctraderapi.com"
    PORT = 5035

    def __init__(self):
        self.client_id = os.environ.get("CTRADER_CLIENT_ID", "")
        self.client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "")
        self.account_id = int(os.environ.get("CTRADER_ACCOUNT_ID", 0))
        self.access_token = os.environ.get("CTRADER_ACCESS_TOKEN", "")

        if not all([self.client_id, self.client_secret, self.account_id, self.access_token]):
            raise ValueError("Missing cTrader credentials in .env")

        self._client = None
        self._message_count = 0

    def _on_tcp_connected(self, client):
        """TCP connected - start auth."""
        logger.info("✅ TCP Connected")
        self._send_app_auth(client)

    def _send_app_auth(self, client):
        """Send app auth."""
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAApplicationAuthReq,
            )
            req = ProtoOAApplicationAuthReq()
            req.clientId = self.client_id
            req.clientSecret = self.client_secret
            logger.info("📤 Sending: ProtoOAApplicationAuthReq")
            d = client.send(req)
            d.addCallback(lambda _: self._send_account_auth(client))
            d.addErrback(lambda f: logger.error(f"❌ App auth failed: {f}"))
        except Exception as e:
            logger.error(f"❌ Error sending app auth: {e}")

    def _send_account_auth(self, client):
        """Send account auth."""
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAAccountAuthReq,
            )
            req = ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = self.account_id
            req.accessToken = self.access_token
            logger.info(f"📤 Sending: ProtoOAAccountAuthReq (account {self.account_id})")
            d = client.send(req)
            d.addErrback(lambda f: logger.error(f"❌ Account auth failed: {f}"))
        except Exception as e:
            logger.error(f"❌ Error sending account auth: {e}")

    def _send_trader_req(self, client):
        """Send trader request."""
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOATraderReq
            req = ProtoOATraderReq()
            req.ctidTraderAccountId = self.account_id
            logger.info("📤 Sending: ProtoOATraderReq")
            d = client.send(req)
            d.addErrback(lambda f: logger.error(f"❌ Trader req failed: {f}"))
        except Exception as e:
            logger.error(f"❌ Error sending trader req: {e}")

    def _send_symbols_list_req(self, client):
        """Send symbols list request."""
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolsListReq
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId = self.account_id
            logger.info("📤 Sending: ProtoOASymbolsListReq")
            d = client.send(req)
            d.addErrback(lambda f: logger.error(f"❌ Symbols req failed: {f}"))
        except Exception as e:
            logger.error(f"❌ Error sending symbols req: {e}")

    def _on_message(self, client, message):
        """
        DIAGNOSTIC MESSAGE HANDLER.
        Print ALL message details to understand structure.
        """
        self._message_count += 1
        
        print("\n" + "=" * 100)
        print(f"📨 MESSAGE #{self._message_count} RECEIVED")
        print("=" * 100)
        
        # 1. Print type
        print(f"\n1️⃣ TYPE:")
        print(f"   type(message) = {type(message)}")
        print(f"   type(message).__name__ = {type(message).__name__}")
        print(f"   type(message).__module__ = {type(message).__module__}")
        
        # 2. Print repr
        print(f"\n2️⃣ REPR:")
        print(f"   {repr(message)[:200]}...")
        
        # 3. Print string
        print(f"\n3️⃣ STRING:")
        print(f"   {str(message)[:200]}...")
        
        # 4. Print attributes
        print(f"\n4️⃣ ATTRIBUTES (dir()):")
        attrs = [a for a in dir(message) if not a.startswith('_')]
        for i, attr in enumerate(attrs[:20]):
            try:
                val = getattr(message, attr)
                if not callable(val):
                    val_str = str(val)[:80]
                    print(f"   {attr}: {val_str}")
            except:
                pass
        if len(attrs) > 20:
            print(f"   ... and {len(attrs) - 20} more attributes")
        
        # 5. Check for common field names
        print(f"\n5️⃣ COMMON FIELDS CHECK:")
        fields_to_check = [
            'trader', 'symbol', 'symbols', 'account', 'id', 'response',
            'payloadType', 'payload', 'executionPrice', 'orderId', 'positionId'
        ]
        for field in fields_to_check:
            if hasattr(message, field):
                try:
                    val = getattr(message, field)
                    print(f"   ✓ {field}: {type(val).__name__} = {str(val)[:60]}")
                except:
                    print(f"   ✓ {field}: [error reading value]")
        
        # 6. Check if it's a wrapper (payload pattern)
        print(f"\n6️⃣ WRAPPER CHECK:")
        if hasattr(message, 'payloadType'):
            print(f"   ✓ Has payloadType: {message.payloadType}")
        if hasattr(message, 'payload'):
            print(f"   ✓ Has payload: {type(message.payload).__name__}")
        
        # 7. Check for Protobuf fields
        print(f"\n7️⃣ PROTOBUF DESCRIPTOR:")
        try:
            if hasattr(message, 'DESCRIPTOR'):
                desc = message.DESCRIPTOR
                print(f"   Full name: {desc.full_name}")
                print(f"   Fields: {[f.name for f in desc.fields][:10]}")
        except:
            print("   [No DESCRIPTOR found]")
        
        print("=" * 100 + "\n")
        
        # After auth, request data
        if self._message_count == 2:  # After account auth succeeds
            time.sleep(1)
            self._send_trader_req(client)
            self._send_symbols_list_req(client)

    def _on_disconnect(self, client, reason):
        logger.warning(f"⚠️ Disconnected: {reason}")

    def _on_error(self, context, failure):
        error_msg = (
            failure.getErrorMessage() if hasattr(failure, 'getErrorMessage')
            else str(failure)
        )
        logger.error(f"❌ Error ({context}): {error_msg}")

    def connect(self, timeout: float = 30.0) -> bool:
        """Connect and run diagnostic."""
        try:
            from ctrader_open_api import Client, TcpProtocol
            from twisted.internet import reactor

            client = Client(self.DEMO_HOST, self.PORT, TcpProtocol)
            self._client = client

            # Register callbacks
            client.setConnectedCallback(self._on_tcp_connected)
            client.setMessageReceivedCallback(self._on_message)
            client.setDisconnectedCallback(self._on_disconnect)
            client.setErrorCallback(lambda _, f: self._on_error("protocol", f))

            connected = threading.Event()
            start_time = time.time()

            def check_timeout():
                if time.time() - start_time > timeout:
                    logger.info("⏱️ Timeout reached, stopping...")
                    reactor.callFromThread(client.stopService)
                    connected.set()
                else:
                    reactor.callLater(1, check_timeout)

            # Start reactor
            if not reactor.running:
                logger.info("🚀 Starting Twisted reactor...")
                t = threading.Thread(
                    target=reactor.run,
                    kwargs={"installSignalHandlers": False},
                    daemon=True
                )
                t.start()
                time.sleep(0.1)

            logger.info(f"🔌 Connecting to {self.DEMO_HOST}:{self.PORT}...")
            reactor.callFromThread(client.startService)
            reactor.callFromThread(check_timeout)
            connected.wait(timeout=timeout + 2)

            logger.info(f"\n✅ Diagnostic complete. Received {self._message_count} messages.")
            return True

        except Exception as e:
            logger.error(f"❌ Diagnostic failed: {e}", exc_info=True)
            return False


def main():
    """Run diagnostic."""
    logger.info("🧪 cTrader Message Structure Diagnostic")
    logger.info("=" * 100)
    logger.info("This will connect to cTrader and print ALL received messages.")
    logger.info("Use this output to fix the Message Dispatcher.\n")

    try:
        diag = CTraderDiagnostic()
        diag.connect(timeout=25.0)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        return False

    logger.info("\n" + "=" * 100)
    logger.info("📋 SUMMARY:")
    logger.info("1. Look at the MESSAGE TYPE and ATTRIBUTES above")
    logger.info("2. Find the correct field names for trader/symbol data")
    logger.info("3. Update ctrader_client.py _on_message() accordingly")
    logger.info("=" * 100)

    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
