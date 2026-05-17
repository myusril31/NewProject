import os
import time
import hmac
import hashlib
import logging
from typing import Any, Dict, List, Optional
import json
from pathlib import Path

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BINANCE_TESTNET_BASE = "https://testnet.binancefuture.com"
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
USE_TESTNET_ONLY = os.getenv("USE_TESTNET_ONLY", "1") == "1"
ALLOW_PROTECTION_CANCEL = os.getenv("ALLOW_PROTECTION_CANCEL", "1") == "1"

PROTECTION_STATE_FILE = Path(os.getenv("PROTECTION_STATE_FILE", "app/protection_state.json"))


def _require_testnet_guard() -> Optional[Any]:
    if not USE_TESTNET_ONLY:
        return jsonify({"ok": False, "error": "testnet_only_guard_failed"}), 403
    return None


def _signed_request(method: str, path: str, params: Dict[str, Any]) -> Any:
    if path == "/fapi/v1/order" and params.get("_protection_cancel"):
        return {"ok": False, "reason": "legacy_order_cancel_for_protection_blocked"}, 400
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        return {
            "ok": False,
            "error": "missing_testnet_api_credentials",
            "detail": "Only TESTNET credentials are supported.",
        }, 400

    payload = {k: v for k, v in params.items() if v is not None}
    payload["timestamp"] = int(time.time() * 1000)
    query = "&".join(f"{k}={payload[k]}" for k in sorted(payload))
    signature = hmac.new(
        BINANCE_API_SECRET.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    payload["signature"] = signature

    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    url = f"{BINANCE_TESTNET_BASE}{path}"
    response = requests.request(method, url, params=payload, headers=headers, timeout=15)
    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}
    return data, response.status_code


def binance_testnet_open_algo_orders(symbol: str):
    return _signed_request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})


def binance_testnet_cancel_algo_order(
    symbol: str, algo_id: Optional[str] = None, client_algo_id: Optional[str] = None
):
    if not algo_id and not client_algo_id:
        return {"ok": False, "error": "missing_algo_identifier"}, 400
    params = {"symbol": symbol}
    if algo_id:
        params["algoId"] = algo_id
    else:
        params["clientAlgoId"] = client_algo_id
    return _signed_request("DELETE", "/fapi/v1/algoOrder", params)


def binance_testnet_cancel_all_algo_orders(symbol: str):
    return _signed_request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol})


def binance_get_position_risk(symbol: str):
    return _signed_request("GET", "/fapi/v2/positionRisk", {"symbol": symbol})




def _load_protection_records(symbol: str) -> List[Dict[str, Any]]:
    """Load persisted protection records for a symbol from existing state storage."""
    try:
        raw = json.loads(PROTECTION_STATE_FILE.read_text()) if PROTECTION_STATE_FILE.exists() else {}
    except Exception:
        logger.exception("Failed to read protection state file: %s", PROTECTION_STATE_FILE)
        raw = {}

    symbol_records = raw.get(symbol, []) if isinstance(raw, dict) else []
    if isinstance(symbol_records, list):
        return [r for r in symbol_records if isinstance(r, dict)]
    return []



def _append_execution_event(event: Dict[str, Any]) -> None:
    events_path = Path("app/execution_events.jsonl")
    events_path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": int(time.time() * 1000), **event}
    with events_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

def _extract_symbol() -> Optional[str]:
    symbol = request.args.get("symbol")
    if not symbol and request.is_json:
        body = request.get_json(silent=True) or {}
        symbol = body.get("symbol")
    if symbol:
        symbol = str(symbol).upper().strip()
    return symbol


@app.route("/testnet/algo-open-orders", methods=["GET", "POST"])
def testnet_algo_open_orders():
    guard = _require_testnet_guard()
    if guard:
        return guard

    symbol = _extract_symbol()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol_required"}), 400

    data, status = binance_testnet_open_algo_orders(symbol)
    return jsonify(data), status


@app.route("/testnet/cancel-protection", methods=["POST"])
def testnet_cancel_protection():
    guard = _require_testnet_guard()
    if guard:
        return guard
    if not ALLOW_PROTECTION_CANCEL:
        return jsonify({"ok": False, "error": "cancel_guard_failed"}), 403

    symbol = _extract_symbol()
    if not symbol:
        return jsonify({"ok": False, "error": "symbol_required"}), 400

    # Hard guard: never allow legacy order cancel path for protection.
    requested_path = (request.get_json(silent=True) or {}).get("cancelPath")
    if requested_path == "/fapi/v1/order":
        return (
            jsonify(
                {
                    "ok": False,
                    "reason": "legacy_order_cancel_for_protection_blocked",
                }
            ),
            400,
        )

    results = []
    cancel_count = 0
    protection_records = _load_protection_records(symbol)
    cancel_failed = False

    for record in protection_records:
        algo_id = record.get("algoId")
        client_algo_id = record.get("clientAlgoId")
        logger.info(
            "Cancel protection attempt symbol=%s algoId=%s clientAlgoId=%s",
            symbol,
            algo_id,
            client_algo_id,
        )
        _append_execution_event({"event": "cancel_protection_attempt", "symbol": symbol, "algoId": algo_id, "clientAlgoId": client_algo_id})

        data, status = binance_testnet_cancel_algo_order(symbol, algo_id=algo_id, client_algo_id=client_algo_id)
        success = 200 <= status < 300
        if success:
            cancel_count += 1
        else:
            cancel_failed = True
        _append_execution_event({"event": "cancel_protection_result", "symbol": symbol, "algoId": algo_id, "clientAlgoId": client_algo_id, "status": status, "success": success})
        results.append(
            {
                "symbol": symbol,
                "algoId": algo_id,
                "clientAlgoId": client_algo_id,
                "status": status,
                "response": data,
                "success": success,
            }
        )

    if not protection_records or cancel_failed:
        logger.info("Fallback cancel-all algo orders for symbol=%s", symbol)
        _append_execution_event({"event": "cancel_protection_fallback_all", "symbol": symbol})
        data, status = binance_testnet_cancel_all_algo_orders(symbol)
        success = 200 <= status < 300
        if success:
            cancel_count += 1
        results.append(
            {
                "symbol": symbol,
                "action": "cancel_all_algo_orders",
                "status": status,
                "response": data,
                "success": success,
            }
        )

    # Final position zero verification: only pass when abs(positionAmt) == 0.
    pos_data, pos_status = binance_get_position_risk(symbol)
    final_position_zero = False
    if 200 <= pos_status < 300:
        if isinstance(pos_data, list) and pos_data:
            item = pos_data[0]
        else:
            item = pos_data if isinstance(pos_data, dict) else {}
        try:
            final_position_zero = abs(float(item.get("positionAmt", "0"))) == 0
        except Exception:
            final_position_zero = False

    return (
        jsonify(
            {
                "ok": True,
                "symbol": symbol,
                "cancel_count": cancel_count,
                "results": results,
                "position_risk_status": pos_status,
                "final_position_zero": final_position_zero,
            }
        ),
        200,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
