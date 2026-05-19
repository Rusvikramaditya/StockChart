"""Phase 6A tests for broad NSE universe construction."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

import pandas as pd

from engine import universe


class UniversePhase6Test(unittest.TestCase):
    def _write_broad_universe(self, path: Path) -> None:
        rows = [
            {
                "symbol": "MRPL",
                "company_name": "Mangalore Refinery & Petroleum",
                "security_id": "2283",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
                "instrument_type": "ES",
                "series": "EQ",
                "lot_size": "1",
                "listing_date": "",
                "status": "ACTIVE",
            },
            {
                "symbol": "MAZDOCK",
                "company_name": "Mazagon Dock Shipbuilders",
                "security_id": "509",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
                "instrument_type": "ES",
                "series": "EQ",
                "lot_size": "1",
                "listing_date": "",
                "status": "ACTIVE",
            },
            {
                "symbol": "AEROFLEX",
                "company_name": "Aeroflex Industries",
                "security_id": "18268",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
                "instrument_type": "ES",
                "series": "EQ",
                "lot_size": "1",
                "listing_date": "",
                "status": "ACTIVE",
            },
            {
                "symbol": "INACTIVE",
                "company_name": "Inactive Company",
                "security_id": "999",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
                "instrument_type": "ES",
                "series": "EQ",
                "lot_size": "1",
                "listing_date": "",
                "status": "INACTIVE",
            },
        ]
        pd.DataFrame(rows, columns=universe.OUTPUT_COLUMNS).to_csv(path, index=False)

    def test_build_all_nse_equity_universe_filters_and_dedupes_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            master_path = tmp_path / "master.csv"
            output_path = tmp_path / "all_nse_equity.csv"
            master_path.write_text(
                textwrap.dedent(
                    """\
                    SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXCH_INSTRUMENT_TYPE,SEM_SERIES,SM_SYMBOL_NAME
                    NSE,E,100,EQUITY,ARE&M,1.0,Amara Raja Energy & Mobility,ES,EQ,AMARA RAJA ENERGY
                    NSE,E,200,EQUITY,AEROFLEX,1.0,Aeroflex Industries,ES,SM,AEROFLEX INDUSTRIES
                    NSE,E,300,EQUITY,DEBTTEST,100.0,Debt Test,DBT,SG,DEBT TEST
                    BSE,E,400,EQUITY,BSEONLY,1.0,BSE Only,ES,EQ,BSE ONLY
                    NSE,E,,EQUITY,MISSINGID,1.0,Missing Id,ES,EQ,MISSING ID
                    NSE,E,500,EQUITY,DUPL,1.0,Duplicate BE,ES,BE,DUPLICATE BE
                    NSE,E,499,EQUITY,DUPL,1.0,Duplicate EQ,ES,EQ,DUPLICATE EQ
                    NSE,E,600,EQUITY,ETFTEST,1.0,ETF Test,ETF,EQ,ETF TEST
                    NSE,E,700,EQUITY,BZCO,1.0,BZ Company,ES,BZ,BZ COMPANY
                    NSE,E,800,EQUITY,PPTEST,1.0,Partly Paid Test,ES,E1,PARTLY PAID TEST
                    """
                ),
                encoding="utf-8",
            )

            result = universe.build_all_nse_equity_universe(
                master_path=master_path,
                output_path=output_path,
                chunksize=2,
            )

            self.assertEqual(result.rows, 5)
            self.assertEqual(result.duplicates_removed, 1)
            written = pd.read_csv(output_path, dtype=str).fillna("")
            self.assertEqual(list(written.columns), universe.OUTPUT_COLUMNS)
            self.assertEqual(
                written["symbol"].tolist(),
                ["AEROFLEX", "ARE&M", "BZCO", "DUPL", "PPTEST"],
            )
            self.assertEqual(written.loc[written["symbol"].eq("DUPL"), "security_id"].item(), "499")
            self.assertEqual(set(written["exchange_segment"]), {"NSE_EQ"})
            self.assertEqual(set(written["instrument_type"]), {"ES"})
            self.assertEqual(set(written["status"]), {"ACTIVE"})
            self.assertEqual(
                written.loc[written["symbol"].eq("ARE&M"), "company_name"].item(),
                "Amara Raja Energy & Mobility",
            )

    def test_resolve_master_columns_fails_fast_without_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            master_path = Path(tmp) / "bad_master.csv"
            master_path.write_text(
                "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL\n"
                "NSE,E,EQUITY,ABC\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(universe.UniverseBuildError, "security_id"):
                universe.resolve_master_columns(master_path)

    def test_profile_loading_resolves_symbol_only_profiles_from_broad_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            profile_path = tmp_path / "nifty500.csv"
            self._write_broad_universe(broad_path)
            profile_path.write_text("symbol\nmazdock\nMRPL\n", encoding="utf-8")

            loaded = universe.load_universe_profile(
                "nifty500",
                profile_path=profile_path,
                broad_path=broad_path,
            )

            self.assertEqual(loaded["symbol"].tolist(), ["MAZDOCK", "MRPL"])
            self.assertEqual(loaded["security_id"].tolist(), ["509", "2283"])
            self.assertEqual(list(loaded.columns), universe.OUTPUT_COLUMNS)

    def test_watchlist_build_resolves_ids_from_broad_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            output_path = tmp_path / "watchlist.csv"
            self._write_broad_universe(broad_path)

            result = universe.build_watchlist_profile(
                ["MRPL", "AEROFLEX"],
                output_path=output_path,
                broad_path=broad_path,
            )

            self.assertEqual(result.symbols, ("MRPL", "AEROFLEX"))
            written = pd.read_csv(output_path, dtype=str).fillna("")
            self.assertEqual(written["security_id"].tolist(), ["2283", "18268"])

    def test_profile_blank_security_id_resolves_from_broad_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            profile_path = tmp_path / "nifty500.csv"
            self._write_broad_universe(broad_path)
            profile_path.write_text(
                "symbol,security_id,exchange_segment,instrument,status\n"
                "MRPL,,NSE_EQ,EQUITY,ACTIVE\n",
                encoding="utf-8",
            )

            loaded = universe.load_universe_profile(
                "nifty500",
                profile_path=profile_path,
                broad_path=broad_path,
            )

            self.assertEqual(loaded["symbol"].tolist(), ["MRPL"])
            self.assertEqual(loaded["security_id"].tolist(), ["2283"])

    def test_nifty500_profile_drops_unresolved_blank_id_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            profile_path = tmp_path / "nifty500.csv"
            self._write_broad_universe(broad_path)
            profile_path.write_text(
                "symbol,security_id,exchange_segment,instrument,status\n"
                "MRPL,,NSE_EQ,EQUITY,ACTIVE\n"
                "DUMMYVEDL1,,NSE_EQ,EQUITY,ACTIVE\n",
                encoding="utf-8",
            )

            loaded = universe.load_universe_profile(
                "nifty500",
                profile_path=profile_path,
                broad_path=broad_path,
            )

            self.assertEqual(loaded["symbol"].tolist(), ["MRPL"])
            self.assertEqual(loaded.attrs["unresolved_symbols"], ("DUMMYVEDL1",))

    def test_nifty500_profile_repairs_stale_security_ids_from_broad_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            profile_path = tmp_path / "nifty500.csv"
            self._write_broad_universe(broad_path)
            profile_path.write_text(
                "symbol,security_id,exchange_segment,instrument,status\n"
                "MRPL,111,NSE_EQ,EQUITY,ACTIVE\n",
                encoding="utf-8",
            )

            loaded = universe.load_universe_profile(
                "nifty500",
                profile_path=profile_path,
                broad_path=broad_path,
            )

            self.assertEqual(loaded["security_id"].tolist(), ["2283"])
            self.assertIn("MRPL expected 2283 got 111", loaded.attrs["security_id_mismatches"])

    def test_missing_watchlist_symbol_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            broad_path = Path(tmp) / "all_nse_equity.csv"
            self._write_broad_universe(broad_path)

            with self.assertRaisesRegex(universe.UniverseProfileError, "UNKNOWN"):
                universe.resolve_symbols_from_broad(["MRPL", "UNKNOWN"], broad_path=broad_path)

    def test_profile_with_guessed_security_id_fails_against_broad_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            profile_path = tmp_path / "watchlist.csv"
            self._write_broad_universe(broad_path)
            profile_path.write_text(
                "symbol,security_id,exchange_segment,instrument,status\n"
                "MRPL,111,NSE_EQ,EQUITY,ACTIVE\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(universe.UniverseProfileError, "security_id mismatch"):
                universe.load_universe_profile(
                    "watchlist",
                    profile_path=profile_path,
                    broad_path=broad_path,
                )

    def test_data_derived_profiles_fail_clearly_until_inputs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            broad_path = tmp_path / "all_nse_equity.csv"
            self._write_broad_universe(broad_path)

            with self.assertRaisesRegex(universe.UniverseProfileError, "refusing to fake"):
                universe.load_universe_profile(
                    "small_mid_liquid",
                    profile_path=tmp_path / "small_mid_liquid.csv",
                    broad_path=broad_path,
                )

    def test_unknown_profile_name_fails_clearly(self):
        with self.assertRaisesRegex(universe.UniverseProfileError, "Unsupported universe profile"):
            universe.load_universe_profile("not_a_profile", broad_path=Path("missing.csv"))


if __name__ == "__main__":
    unittest.main()
