"""OKX Payment SDK integration stub for A2MCP pay-per-call billing.

A2MCP requires the OKX Payment SDK to be wired before the listing goes live.
This module isolates that seam: the pipeline stays payment-agnostic and the
serving layer calls ``A2MCPBilling.charge()`` once per canonicalized record.

Pricing is expressed in micro-units (1 micro-unit = 1e-6 USDG/USDT) and kept
as ints end-to-end — the no-floats rule applies to billing too.
"""

import os
from dataclasses import dataclass

# Tier 1 (base): validate + canonicalize one record against default/target schema.
PRICE_PER_RECORD_MICRO = 2000     # 0.002 per record (matches x402 endpoint price)
SETTLEMENT_ASSETS = ("USDG", "USDT")


@dataclass
class ChargeReceipt:
    caller_id: str
    records: int
    amount_micro: int
    asset: str
    settled: bool
    tx_ref: str | None


class A2MCPBilling:
    """Per-call billing gateway. Everything money-shaped is an int in micro-units."""

    def __init__(self, asset: str = "USDG",
                 price_per_record_micro: int = PRICE_PER_RECORD_MICRO):
        assert asset in SETTLEMENT_ASSETS
        assert isinstance(price_per_record_micro, int) and price_per_record_micro > 0
        self.asset = asset
        self.price_per_record_micro = price_per_record_micro

    def quote(self, records: int) -> int:
        """Micro-unit price for a batch. Pure int math."""
        assert isinstance(records, int) and records > 0
        return records * self.price_per_record_micro

    def charge(self, caller_id: str, records: int = 1) -> ChargeReceipt:
        amount = self.quote(records)

        # ------------------------------------------------------------------
        # TODO(go-live): attach live OKX Payment SDK credentials + Agentic
        # Wallet here. Required before the A2MCP listing can be published.
        #
        #   from okx_payment_sdk import PaymentClient          # noqa: ERA001
        #   client = PaymentClient(
        #       api_key=os.environ["OKX_PAYMENT_API_KEY"],
        #       wallet_id=os.environ["OKX_AGENTIC_WALLET_ID"],  # keys stay in TEE
        #   )
        #   receipt = client.collect(
        #       payer=caller_id, asset=self.asset,
        #       amount_micro=amount,                             # int micro-units
        #       memo=f"evm-canon x{records}",
        #   )
        #   return ChargeReceipt(caller_id, records, amount, self.asset,
        #                        settled=receipt.settled, tx_ref=receipt.tx_hash)
        # ------------------------------------------------------------------
        if os.environ.get("EVM_CANON_BILLING") == "live":
            raise NotImplementedError(
                "OKX Payment SDK not wired yet — see TODO(go-live) above")

        # Dry-run mode: record the would-be charge without settling.
        return ChargeReceipt(caller_id=caller_id, records=records,
                             amount_micro=amount, asset=self.asset,
                             settled=False, tx_ref=None)
