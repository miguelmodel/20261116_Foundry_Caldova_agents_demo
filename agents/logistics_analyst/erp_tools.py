"""Read-only, deterministic ERP tools over the fictitious Caldova snapshot."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

from langchain_core.tools import tool


DATA_DIR = Path(__file__).resolve().parent / "data"


def _read(name: str):
    return json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))


CATALOG = _read("catalog")
SHIPMENTS = _read("shipments")
INVENTORY = _read("inventory")
FORECAST = _read("demand_forecast")
SUPPLIERS = _read("suppliers")
CARRIERS = _read("carrier_options")
TODAY = date.fromisoformat(CATALOG["as_of_date"])
LAUNCH = date.fromisoformat(CATALOG["launch_date"])
FLAGSHIPS = [key for key, value in CATALOG["locations"].items() if value["type"] == "flagship"]


def _money(value) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def _daily(sku: str) -> dict[str, int]:
    forecast = FORECAST[sku]
    return {key: value * forecast["demand_multiplier"] for key, value in forecast["baseline_daily_units"].items()}


def _shipment(shipment_id: str):
    return next((row for row in SHIPMENTS if row["shipment_id"] == shipment_id), None)


def _unknown_sku(sku: str) -> dict:
    return {"error": f"Unknown SKU: {sku}", "available_skus": list(CATALOG["products"])}


def _approval(cost: float) -> str:
    if cost == 0:
        return "No additional spending; operational sign-off still required"
    if cost < 25000:
        return "Logistics Manager"
    if cost <= 100000:
        return "Head of Supply Chain"
    return "COO"


def _donors(sku: str) -> list[dict]:
    daily = _daily(sku)
    result = []
    for location, stock in INVENTORY[sku].items():
        kind = CATALOG["locations"][location]["type"]
        if kind == "flagship":
            continue
        retained = FORECAST[sku]["dc_safety_stock_units"][location] if kind == "dc" else daily[location] * CATALOG["regular_store_min_cover_days_at_dispatch"]
        available = max(0, stock - retained)
        if available:
            result.append({"location_id": location, "available_units": available, "retained_units": stock - available})
    return result


def _allocate(quantity: int, daily: dict[str, int]) -> dict[str, int]:
    total = sum(daily[location] for location in FLAGSHIPS)
    result = {location: quantity * daily[location] // total for location in FLAGSHIPS}
    remaining = quantity - sum(result.values())
    order = sorted(FLAGSHIPS, key=lambda location: (-(quantity * daily[location] % total), location))
    for location in order[:remaining]:
        result[location] += 1
    return result


def _penalty(shipment: dict, delay_days: int) -> dict:
    terms = SUPPLIERS[shipment["supplier_id"]].get("late_delivery_terms")
    if terms is None:
        return {"potential_recovery_eur": 0.0, "status": "No late-delivery credit terms in this snapshot; do not apply Atlas terms."}
    chargeable = max(0, delay_days - terms["grace_days"])
    fraction = min(Decimal(str(terms["cap_po_fraction"])), Decimal(str(terms["daily_po_fraction"])) * chargeable)
    amount = Decimal(str(shipment["po_value_eur"])) * fraction
    approved_waiver = shipment.get("force_majeure_approved", False)
    notification_hours = None
    if shipment.get("supplier_notification_utc") and shipment.get("delay_awareness_utc"):
        notification_hours = (datetime.fromisoformat(shipment["supplier_notification_utc"]) - datetime.fromisoformat(shipment["delay_awareness_utc"])).total_seconds() / 3600
    return {
        "potential_recovery_eur": 0.0 if approved_waiver else _money(amount),
        "chargeable_late_days": chargeable,
        "daily_po_fraction": terms["daily_po_fraction"],
        "cap_eur": _money(Decimal(str(shipment["po_value_eur"])) * Decimal(str(terms["cap_po_fraction"]))),
        "force_majeure_approved": approved_waiver,
        "notification_hours_after_awareness": notification_hours,
        "notification_within_48_hours": None if notification_hours is None else 0 <= notification_hours <= terms["notification_due_hours"],
        "status": "Waived" if approved_waiver else "Projected claim, not guaranteed cash; separate from recovery cost and approval.",
    }


def _simulate(sku: str, receipts: list[dict], horizon_days: int) -> dict:
    daily = _daily(sku)
    stock = {location: INVENTORY[sku][location] for location in FLAGSHIPS}
    stockouts = dict.fromkeys(FLAGSHIPS, 0)
    lost = dict.fromkeys(FLAGSHIPS, 0)
    first = dict.fromkeys(FLAGSHIPS)
    launch_lost = dict.fromkeys(FLAGSHIPS, 0)
    arrivals = {}
    for receipt in receipts:
        day = (date.fromisoformat(receipt["store_available_date"]) - TODAY).days
        if day < 0:
            continue
        allocation = _allocate(receipt["quantity_units"], daily)
        bucket = arrivals.setdefault(day, dict.fromkeys(FLAGSHIPS, 0))
        for location, quantity in allocation.items():
            bucket[location] += quantity
    for day in range(horizon_days):
        current_date = TODAY + timedelta(days=day)
        for location in FLAGSHIPS:
            stock[location] += arrivals.get(day, {}).get(location, 0)
            shortage = max(0, daily[location] - stock[location])
            stock[location] = max(0, stock[location] - daily[location])
            if shortage:
                stockouts[location] += 1
                lost[location] += shortage
                if first[location] is None:
                    first[location] = current_date.isoformat()
                if current_date == LAUNCH:
                    launch_lost[location] = shortage
    return {
        "flagship_stockout_days": stockouts,
        "flagship_lost_units": lost,
        "first_stockout_date": first,
        "launch_day_shortfall_units": launch_lost,
        "total_lost_units": sum(lost.values()),
    }


@tool
def get_shipment_status(shipment_id: str) -> dict:
    """Look up a fictitious shipment's SKU, supplier, units, PO value, delay cause, DC/store ETAs and projected contractual credit. Dates use the fixed 2026-11-03 ERP snapshot, not today's live date."""
    shipment = _shipment(shipment_id)
    if shipment is None:
        return {"error": f"Unknown shipment: {shipment_id}", "available_shipment_ids": [row["shipment_id"] for row in SHIPMENTS]}
    result = deepcopy(shipment)
    result.update({
        "fictitious_demo_data": True,
        "as_of_date": TODAY.isoformat(),
        "product_name": CATALOG["products"][shipment["sku"]]["name"],
        "supplier_name": SUPPLIERS[shipment["supplier_id"]]["name"],
        "currency": "EUR",
        "store_available_date": (date.fromisoformat(shipment["revised_dc_arrival"]) + timedelta(days=CARRIERS["road"]["dc_to_store_days"])).isoformat(),
        "late_delivery_credit": _penalty(shipment, shipment["delay_days"]),
    })
    return result


@tool
def list_delayed_shipments() -> dict:
    """List delayed Caldova shipments and causes in the fixed fictitious ERP snapshot, including the Atlas linen shipment and the two Ningbo port-congestion shipments."""
    return {
        "as_of_date": TODAY.isoformat(),
        "fictitious_demo_data": True,
        "shipments": [
            {key: row[key] for key in ["shipment_id", "sku", "quantity_units", "delay_days", "cause", "original_dc_arrival", "revised_dc_arrival"]}
            for row in SHIPMENTS if row["status"] == "delayed"
        ],
    }


@tool
def get_inventory_position(sku: str) -> dict:
    """Return opening stock by Caldova DC/store, effective daily demand, days of cover, safety stock and releasable donor units for a SKU. Satin cover uses 4x baseline demand; DC days of cover is null."""
    if sku not in CATALOG["products"]:
        return _unknown_sku(sku)
    daily = _daily(sku)
    donors = {row["location_id"]: row for row in _donors(sku)}
    return {
        "as_of_date": TODAY.isoformat(),
        "sku": sku,
        "product_name": CATALOG["products"][sku]["name"],
        "fictitious_demo_data": True,
        "locations": [
            {
                "location_id": key,
                "name": CATALOG["locations"][key]["name"],
                "type": CATALOG["locations"][key]["type"],
                "stock_units": units,
                "daily_demand_units": daily[key],
                "days_of_cover": round(units / daily[key], 2) if daily[key] else None,
                "minimum_retained_units_at_dispatch": FORECAST[sku]["dc_safety_stock_units"].get(key, daily[key] * 7 if CATALOG["locations"][key]["type"] == "regular" else 0),
                "releasable_units": donors.get(key, {}).get("available_units", 0),
            }
            for key, units in INVENTORY[sku].items()
        ],
        "total_releasable_units": sum(row["available_units"] for row in donors.values()),
    }


@tool
def get_demand_forecast(sku: str) -> dict:
    """Get daily units by location, baseline versus effective demand, multiplier and 30-day totals. A +300% satin viral spike is a 4x multiplier throughout 2026-11-03 to 2026-12-02, not 3x."""
    if sku not in CATALOG["products"]:
        return _unknown_sku(sku)
    forecast = FORECAST[sku]
    days = (date.fromisoformat(CATALOG["forecast_end_date"]) - TODAY).days + 1
    return {
        "sku": sku,
        "product_name": CATALOG["products"][sku]["name"],
        "fictitious_demo_data": True,
        "forecast_start": TODAY.isoformat(),
        "forecast_end": CATALOG["forecast_end_date"],
        "launch_date": LAUNCH.isoformat(),
        "pattern": "Constant daily forecast, inclusive date range; launch already included.",
        "demand_multiplier": forecast["demand_multiplier"],
        "demand_change_percent": (forecast["demand_multiplier"] - 1) * 100,
        "reason": forecast["reason"],
        "locations": [
            {
                "location_id": key,
                "baseline_daily_units": forecast["baseline_daily_units"][key],
                "effective_daily_units": value,
                "forecast_period_units": value * days,
            }
            for key, value in _daily(sku).items()
        ],
    }


def _alternatives(sku: str) -> dict:
    if sku not in CATALOG["products"]:
        return _unknown_sku(sku)
    alternatives = []
    for supplier_id, supplier in SUPPLIERS.items():
        if not supplier.get("contingency_supplier") or sku not in supplier["approved_unit_prices_eur"]:
            continue
        quantity = supplier["available_capacity_units"][sku]
        price = Decimal(str(supplier["approved_unit_prices_eur"][sku]))
        road = Decimal(str(CARRIERS["road"]["eur_per_unit"]))
        dc_date = TODAY + timedelta(days=supplier["lead_days_to_dc_including_road"])
        alternatives.append({
            "supplier_id": supplier_id,
            "supplier_name": supplier["name"],
            "origin": f"{supplier['city']}, {supplier['country']}",
            "unit_cost_eur": float(price),
            "goods_price_premium_percent": supplier["goods_price_premium_fraction"] * 100,
            "available_units": quantity,
            "minimum_order_units": supplier["minimum_order_units"],
            "lead_days_to_dc_including_road": supplier["lead_days_to_dc_including_road"],
            "dc_arrival_date": dc_date.isoformat(),
            "store_available_date": (dc_date + timedelta(days=CARRIERS["road"]["dc_to_store_days"])).isoformat(),
            "full_capacity_landed_store_cost_eur": _money(quantity * (price + 2 * road)),
            "capacity_note": supplier["capacity_note"],
        })
    return {"sku": sku, "as_of_date": TODAY.isoformat(), "currency": "EUR", "alternatives": alternatives, "note": "Additional purchase; the original PO is not cancelled."}


@tool
def find_alternative_suppliers(sku: str) -> dict:
    """Find approved contingency suppliers for a SKU with snapshot capacity, unit price, premium, minimum order, six-day DC lead and full additional landed-store cash. Does not reserve capacity or issue a PO."""
    return _alternatives(sku)


@tool
def evaluate_recovery_options(sku: str, shipment_id: str, delay_days: int) -> dict:
    """Compute wait, up-to-3,000-unit air diversion, backup capacity, safe stock reallocation and simple combinations. Returns per-flagship stockout days/lost units, additional cash EUR, approval and potential Atlas credit. delay_days is TOTAL lateness versus original DC ETA, not extra lateness on top of the recorded delay. No bookings or model calls."""
    if sku not in CATALOG["products"]:
        return _unknown_sku(sku)
    shipment = _shipment(shipment_id)
    if shipment is None:
        return {"error": f"Unknown shipment: {shipment_id}"}
    if shipment["sku"] != sku:
        return {"error": f"{shipment_id} contains {shipment['sku']}, not {sku}"}
    if delay_days < 0 or delay_days > 60:
        return {"error": "delay_days must be total calendar-day lateness between 0 and 60"}
    if shipment["status"] == "delivered":
        return {"error": "Delivered stock is already included in opening inventory; select an open shipment."}
    store_date = date.fromisoformat(shipment["original_dc_arrival"]) + timedelta(days=delay_days + CARRIERS["road"]["dc_to_store_days"])
    horizon_end = max(LAUNCH, store_date - timedelta(days=1))
    if store_date < TODAY or horizon_end > date.fromisoformat(CATALOG["forecast_end_date"]):
        return {"error": "Scenario falls outside the available forecast window; no extrapolation performed."}
    horizon_days = (horizon_end - TODAY).days + 1
    lane = CARRIERS["lanes"][shipment["lane"]]
    road = Decimal(str(CARRIERS["road"]["eur_per_unit"]))
    donors = _donors(sku)
    reallocation_units = sum(row["available_units"] for row in donors)
    alternatives = _alternatives(sku)["alternatives"]
    air_units = min(shipment["quantity_units"], lane["air_capacity_units"]) if shipment.get("air_diversion_available") and shipment.get("split_decision_deadline", TODAY.isoformat()) >= TODAY.isoformat() else 0
    specs = [("wait", 0, None, 0)]
    if air_units:
        specs.append(("partial_air_freight", air_units, None, 0))
    for alternative in alternatives:
        specs.append(("backup_supplier", 0, alternative, 0))
    if reallocation_units:
        specs.append(("stock_reallocation", 0, None, reallocation_units))
        if air_units:
            specs.append(("reallocation_plus_air", air_units, None, reallocation_units))
        for alternative in alternatives:
            specs.append(("reallocation_plus_backup", 0, alternative, reallocation_units))
    other_receipts = [
        {"mode": "other_existing_po", "shipment_id": row["shipment_id"], "quantity_units": row["quantity_units"], "store_available_date": (date.fromisoformat(row["revised_dc_arrival"]) + timedelta(days=CARRIERS["road"]["dc_to_store_days"])).isoformat()}
        for row in SHIPMENTS if row["sku"] == sku and row["shipment_id"] != shipment_id and row["status"] != "delivered"
    ]
    options = []
    for name, air_quantity, backup, transfer_quantity in specs:
        receipts = deepcopy(other_receipts)
        receipts.append({"mode": "residual_sea", "quantity_units": shipment["quantity_units"] - air_quantity, "store_available_date": store_date.isoformat()})
        costs = {"air_freight_premium_eur": Decimal("0"), "new_backup_goods_eur": Decimal("0"), "backup_road_both_legs_eur": Decimal("0"), "reallocation_road_eur": transfer_quantity * road}
        notes = []
        if air_quantity:
            receipts.append({"mode": "air_diversion", "quantity_units": air_quantity, "store_available_date": (TODAY + timedelta(days=lane["air_lead_days_to_dc"] + CARRIERS["road"]["dc_to_store_days"])).isoformat()})
            costs["air_freight_premium_eur"] = air_quantity * (Decimal(str(lane["air_eur_per_unit"])) - Decimal(str(lane["sea_eur_per_unit"])))
            notes.append("Diverts existing PO units; full unused-sea credit assumed; carrier confirmation required.")
        if backup:
            quantity = backup["available_units"]
            receipts.append({"mode": "additional_backup_purchase", "quantity_units": quantity, "store_available_date": backup["store_available_date"]})
            costs["new_backup_goods_eur"] = quantity * Decimal(str(backup["unit_cost_eur"]))
            costs["backup_road_both_legs_eur"] = quantity * 2 * road
            notes.append(f"{backup['supplier_name']}: additional purchase, original PO remains open; capacity not reserved.")
        if transfer_quantity:
            receipts.append({"mode": "direct_reallocation", "quantity_units": transfer_quantity, "store_available_date": (TODAY + timedelta(days=CARRIERS["road"]["direct_reallocation_days"])).isoformat()})
            notes.append("DC reserves and seven donor-store demand days retained at dispatch only; later donor replenishment not modeled.")
        cash = _money(sum(costs.values()))
        options.append({
            "option": name,
            "cost_eur": cash,
            "cost_breakdown_eur": {key: _money(value) for key, value in costs.items()},
            "required_approver": _approval(cash),
            "receipts": receipts,
            **_simulate(sku, receipts, horizon_days),
            "notes": notes,
        })
    best = min(options, key=lambda row: (row["total_lost_units"], row["cost_eur"]))
    return {
        "fictitious_demo_data": True,
        "as_of_date": TODAY.isoformat(),
        "sku": sku,
        "shipment_id": shipment_id,
        "currency": "EUR",
        "delay_days_assumed": delay_days,
        "recorded_delay_days": shipment["delay_days"],
        "revised_dc_arrival_assumed": (store_date - timedelta(days=CARRIERS["road"]["dc_to_store_days"])).isoformat(),
        "store_available_date_assumed": store_date.isoformat(),
        "launch_date": LAUNCH.isoformat(),
        "evaluation_start": TODAY.isoformat(),
        "evaluation_end_inclusive": horizon_end.isoformat(),
        "flagships": {key: CATALOG["locations"][key]["name"] for key in FLAGSHIPS},
        "donor_plan": donors,
        "potential_supplier_credit": _penalty(shipment, delay_days),
        "options": options,
        "lowest_shortfall_then_cost_option": best["option"],
        "assumptions": [
            "Fixed 2026-11-03 snapshot; all actions decided immediately. No live quotes or transactions.",
            "Capacity-sized comparison, not a minimum-quantity optimizer: air uses up to 3,000 units; backup uses the available SKU capacity. Right-size the booking before approval.",
            "Additional cash over committed original PO/sea/final-mile budgets; credit is separate, never netted for approval.",
            "All replenishment allocated proportionally to effective flagship demand with integer rounding; regular-store service is not optimized.",
            "Receipts precede daily sales; any partial daily shortage counts as one stockout day; unmet units are lost, not backordered.",
            "Demand is constant over the forecast window; satin uses 4x baseline. Delivered shipments are already in opening stock.",
            "No tax, customs surcharge, carrying cost, emissions, lost-margin valuation or original-PO cancellation is modeled.",
        ],
    }


ERP_TOOLS = [
    get_shipment_status,
    list_delayed_shipments,
    get_inventory_position,
    get_demand_forecast,
    find_alternative_suppliers,
    evaluate_recovery_options,
]
