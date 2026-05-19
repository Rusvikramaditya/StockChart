"""Phase 6A tests for broad NSE universe construction."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

import pandas as pd

from engine import universe


class BroadUniversePhase6ATest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
