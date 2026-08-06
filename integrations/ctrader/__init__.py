"""integrations/ctrader — cTrader OAuth 2.0 credential-acquisition layer.

This package NEVER opens a live cTrader trading session. It only ever:
  - builds the OAuth authorize URL and exchanges/refreshes tokens over
    plain HTTPS (oauth.py),
  - tracks token expiry and persists tokens to .env (token_manager.py),
  - does a one-shot, throwaway account-discovery connection (account.py).

execution/ctrader_client.py remains the single implementation of the live
session state machine, the cross-process lock, and order placement — this
package only ever supplies it a fresh access_token.
"""
