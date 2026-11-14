"""Offline regression checks for the three fictitious disruption scenarios."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import patch

from agents.logistics_analyst import erp_tools as erp


class ERPToolTests(unittest.TestCase):
    def evaluate(self, sku="CV-LIN-0042", shipment="SH-2041", delay=9):
        return erp.evaluate_recovery_options.invoke({"sku": sku, "shipment_id": shipment, "delay_days": delay})

    def test_linen_recovery_cost_stockouts_and_approval(self):
        result = self.evaluate()
        options = {row["option"]: row for row in result["options"]}
        self.assertEqual(result["potential_supplier_credit"]["potential_recovery_eur"], 16740)
        self.assertTrue(result["potential_supplier_credit"]["notification_within_48_hours"])
        self.assertEqual(options["wait"]["flagship_stockout_days"], {"ES-MAD-GV": 13, "ES-BCN-PG": 13, "FR-PAR-RI": 14, "IT-MIL-CV": 14})
        combined = options["reallocation_plus_air"]
        self.assertEqual(sum(combined["flagship_stockout_days"].values()), 0)
        self.assertEqual(Decimal(str(combined["cost_eur"])), 3000 * (Decimal("9.80") - Decimal("1.20")) + 3475 * Decimal("0.65"))
        self.assertEqual(combined["required_approver"], "Head of Supply Chain")
        self.assertEqual(Decimal(str(options["backup_supplier"]["cost_eur"])), 5000 * (Decimal("18.29") + 2 * Decimal("0.65")))
        self.assertEqual(options["reallocation_plus_backup"]["required_approver"], "COO")
        self.assertEqual(result["lowest_shortfall_then_cost_option"], "reallocation_plus_air")
        self.assertEqual(sum(row["quantity_units"] for row in combined["receipts"] if row["mode"] in {"residual_sea", "air_diversion"}), 12000)
        self.assertEqual(sum(row["available_units"] for row in result["donor_plan"]), 3475)
        self.assertEqual(self.evaluate(), result)

    def test_penalty_grace_cap_and_force_majeure(self):
        self.assertEqual(self.evaluate(delay=3)["potential_supplier_credit"]["potential_recovery_eur"], 0)
        self.assertEqual(self.evaluate(delay=4)["potential_supplier_credit"]["potential_recovery_eur"], 2790)
        self.assertEqual(self.evaluate(delay=13)["potential_supplier_credit"]["potential_recovery_eur"], 27900)
        shipments = deepcopy(erp.SHIPMENTS)
        shipments[0]["force_majeure_approved"] = True
        with patch.object(erp, "SHIPMENTS", shipments):
            self.assertEqual(self.evaluate()["potential_supplier_credit"]["potential_recovery_eur"], 0)

    def test_ningbo_affects_both_skus_without_atlas_penalties(self):
        delayed = erp.list_delayed_shipments.invoke({})["shipments"]
        ningbo = [row for row in delayed if "Ningbo" in row["cause"]]
        self.assertEqual({row["shipment_id"] for row in ningbo}, {"SH-2043", "SH-2047"})
        for shipment in ningbo:
            result = self.evaluate(shipment["sku"], shipment["shipment_id"], shipment["delay_days"])
            options = {row["option"]: row for row in result["options"]}
            self.assertGreater(options["wait"]["total_lost_units"], 0)
            self.assertLess(options["stock_reallocation"]["total_lost_units"], options["wait"]["total_lost_units"])
            self.assertEqual(result["potential_supplier_credit"]["potential_recovery_eur"], 0)
            self.assertNotIn("backup_supplier", options)
            self.assertEqual(options["partial_air_freight"]["cost_eur"], 28200)

    def test_viral_spike_changes_cover_and_donor_protection(self):
        forecast = erp.get_demand_forecast.invoke({"sku": "CV-SAT-0117"})
        self.assertEqual(forecast["demand_change_percent"], 300)
        daily = {row["location_id"]: row for row in forecast["locations"]}
        self.assertEqual(daily["ES-MAD-GV"]["effective_daily_units"], 112)
        self.assertEqual(daily["ES-MAD-GV"]["forecast_period_units"], 112 * 30)
        inventory = erp.get_inventory_position.invoke({"sku": "CV-SAT-0117"})
        positions = {row["location_id"]: row for row in inventory["locations"]}
        self.assertAlmostEqual(positions["ES-MAD-GV"]["days_of_cover"], 900 / 112, places=2)
        self.assertIsNone(positions["DC-GUA"]["days_of_cover"])
        self.assertEqual(positions["ES-BCN-DI"]["releasable_units"], 320 - 40 * 7)
        result = self.evaluate("CV-SAT-0117", "SH-2045", 0)
        options = {row["option"]: row for row in result["options"]}
        self.assertGreater(options["wait"]["total_lost_units"], 0)
        self.assertEqual(options["reallocation_plus_air"]["total_lost_units"], 0)
        alternative = erp.find_alternative_suppliers.invoke({"sku": "CV-SAT-0117"})["alternatives"][0]
        self.assertEqual(alternative["full_capacity_landed_store_cost_eur"], 56350)
        self.assertEqual(alternative["store_available_date"], "2026-11-10")

    def test_snapshot_purchase_values_and_lateness_are_consistent(self):
        for shipment in erp.SHIPMENTS:
            self.assertEqual(Decimal(str(shipment["unit_cost_eur"])) * shipment["quantity_units"], Decimal(str(shipment["po_value_eur"])))
            self.assertEqual((date.fromisoformat(shipment["revised_dc_arrival"]) - date.fromisoformat(shipment["original_dc_arrival"])).days, shipment["delay_days"])
        for sku in erp.CATALOG["products"]:
            self.assertEqual(set(erp.INVENTORY[sku]), set(erp._daily(sku)))
        status = erp.get_shipment_status.invoke({"shipment_id": "SH-2041"})
        self.assertEqual(status["store_available_date"], "2026-11-20")
        status["quantity_units"] = 0
        self.assertEqual(erp.get_shipment_status.invoke({"shipment_id": "SH-2041"})["quantity_units"], 12000)

    def test_invalid_or_already_delivered_inputs_do_not_double_count_stock(self):
        for tool in [erp.get_inventory_position, erp.get_demand_forecast, erp.find_alternative_suppliers]:
            self.assertIn("error", tool.invoke({"sku": "UNKNOWN"}))
        self.assertIn("error", erp.get_shipment_status.invoke({"shipment_id": "UNKNOWN"}))
        self.assertIn("error", self.evaluate(shipment="SH-2038", delay=0))
        self.assertIn("error", self.evaluate(sku="CV-SAT-0117"))
        self.assertIn("error", self.evaluate(delay=-1))
        self.assertIn("error", self.evaluate(delay=60))


if __name__ == "__main__":
    unittest.main()
