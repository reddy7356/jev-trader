# AAVE gatekeeper replay

Generated 2026-10-04 17:13 ET by `scripts/gatekeeper_replay.py` from `data/rec_AAVE_full.jsonl` + `data/rec_BTC_full.jsonl`. Paper only: long only, no leverage, risk 0.25% per trade, $100 daily loss limit, taker fees on entry and stops, maker on targets.

New Jev calls this run: 7 ($0.0009); cached answers are reused.

## FAST (every 30 s): code rules only

- Entry checks while flat: 8,144
- Trades: 15 (2 wins, 13 losses)
- Net result after fees: **-53.63** on $10,000
- Average R: -0.35; worst trade -1.08R

| day (ET) | result |
|---|---|
| 2026-10-01 | -20.99 |
| 2026-10-02 | +11.20 |
| 2026-10-03 | -14.76 |
| 2026-10-04 | -29.09 |

| opened (ET) | entry | stop | T1 | exit | minutes | $ | R |
|---|---|---|---|---|---|---|---|
| Thu 10-01 18:40 | 171.58 | 170.79 | 173.11 | stop | 4.9 | -19.51 | -1.08 |
| Thu 10-01 20:23 | 172.79 | 170.72 | 176.23 | AAVE bids collapsed and sellers dominate | 4.6 | -1.47 | -0.06 |
| Fri 10-02 00:24 | 178.33 | 176.24 | 181.82 | T1: sold 50%; AAVE bids collapsed and sellers dominate | 5.5 | +39.16 | +1.57 |
| Fri 10-02 00:43 | 184.03 | 181.11 | 188.76 | AAVE bids collapsed and sellers dominate | 5.4 | -1.79 | -0.07 |
| Fri 10-02 02:45 | 185.80 | 184.10 | 188.72 | AAVE bids collapsed and sellers dominate | 1.7 | -6.36 | -0.25 |
| Fri 10-02 11:12 | 183.19 | 181.08 | 186.70 | AAVE bids collapsed and sellers dominate | 13.2 | -7.55 | -0.53 |
| Fri 10-02 14:32 | 182.07 | 181.41 | 183.40 | stop | 4.1 | -15.41 | -1.04 |
| Fri 10-02 15:46 | 180.79 | 179.77 | 182.68 | AAVE bids collapsed and sellers dominate | 6.4 | -3.03 | -0.79 |
| Fri 10-02 16:59 | 180.44 | 179.34 | 182.45 | AAVE bids collapsed and sellers dominate | 23.1 | +6.20 | +0.25 |
| Sat 10-03 06:10 | 181.77 | 180.62 | 183.86 | AAVE bids collapsed and sellers dominate | 4.1 | -0.56 | -0.04 |
| Sat 10-03 16:47 | 181.11 | 179.95 | 183.19 | AAVE bids collapsed and sellers dominate | 18.0 | -8.51 | -0.59 |
| Sat 10-03 20:30 | 182.41 | 181.54 | 184.06 | AAVE bids collapsed and sellers dominate | 5.9 | -5.69 | -0.46 |
| Sat 10-03 23:56 | 182.18 | 181.57 | 183.44 | AAVE bids collapsed and sellers dominate | 20.4 | -2.74 | -0.53 |
| Sun 10-04 07:22 | 180.48 | 179.64 | 182.10 | stop | 43.1 | -10.78 | -1.05 |
| Sun 10-04 11:21 | 179.43 | 178.32 | 181.46 | AAVE bids collapsed and sellers dominate | 5.5 | -15.57 | -0.63 |

Why it said NO TRADE (share of entry checks, a check can have several):

- AAVE bids not stronger than asks for # min: 95%
- AAVE #-min volume # median, needs #: 66%
- AAVE price not accepting higher: 52%
- AAVE aggressive buying # < #: 51%
- scheduled event: US data releases and stock open: 4%
- BTC not stable or rising: 2%
- BTC history incomplete: 1%
- BTC data stale: 0%
- stop # below entry: 0%

## FAST (every 30 s): code rules + Jev

- Entry checks while flat: 8,458
- Trades: 0 (0 wins, 0 losses)
- Net result after fees: **+0.00** on $10,000
- Passed every code rule but Jev said no: 23

| day (ET) | result |
|---|---|

Why it said NO TRADE (share of entry checks, a check can have several):

- AAVE bids not stronger than asks for # min: 94%
- AAVE #-min volume # median, needs #: 66%
- AAVE price not accepting higher: 51%
- AAVE aggressive buying # < #: 51%
- scheduled event: US data releases and stock open: 4%
- BTC not stable or rising: 2%
- BTC history incomplete: 1%
- Jev: bids pulling / offers replenishing p=#: 0%
- Jev: demand accepted by price p=#: 0%
- Jev: BTC sell pressure p=#: 0%
- stop # below entry: 0%
- BTC data stale: 0%

## SLOW (every 300 s): code rules only

- Entry checks while flat: 740
- Trades: 3 (2 wins, 1 losses)
- Net result after fees: **+29.39** on $10,000
- Average R: +0.49; worst trade -0.11R

| day (ET) | result |
|---|---|
| 2026-10-03 | +32.08 |
| 2026-10-04 | -2.69 |

| opened (ET) | entry | stop | T1 | exit | minutes | $ | R |
|---|---|---|---|---|---|---|---|
| Sat 10-03 01:25 | 178.53 | 176.16 | 182.43 | BTC risk-off: BTC data stale | 226.9 | +15.69 | +0.91 |
| Sat 10-03 06:07 | 181.10 | 180.00 | 183.10 | T1: sold 50%; stop at entry after T1 | 128.8 | +16.38 | +0.65 |
| Sat 10-03 20:31 | 182.11 | 180.21 | 185.31 | time stop: no T1 within 240 min | 240.0 | -2.69 | -0.11 |

Why it said NO TRADE (share of entry checks, a check can have several):

- AAVE bids not stronger than asks for # min: 90%
- AAVE #-min volume # median, needs #: 69%
- AAVE aggressive buying # < #: 53%
- AAVE price not accepting higher: 53%
- AAVE not in an uptrend: 51%
- scheduled event: US data releases and stock open: 4%
- BTC not stable or rising: 3%
- BTC history incomplete: 1%
- BTC data stale: 1%
- stop # below entry: 0%

## SLOW (every 300 s): code rules + Jev

- Entry checks while flat: 856
- Trades: 0 (0 wins, 0 losses)
- Net result after fees: **+0.00** on $10,000
- Passed every code rule but Jev said no: 7

| day (ET) | result |
|---|---|

Why it said NO TRADE (share of entry checks, a check can have several):

- AAVE bids not stronger than asks for # min: 86%
- AAVE #-min volume # median, needs #: 68%
- AAVE aggressive buying # < #: 53%
- AAVE price not accepting higher: 51%
- AAVE not in an uptrend: 49%
- scheduled event: US data releases and stock open: 4%
- BTC not stable or rising: 3%
- BTC history incomplete: 1%
- Jev: bids pulling / offers replenishing p=#: 1%
- Jev: demand accepted by price p=#: 1%
- BTC data stale: 1%
- Jev: BTC sell pressure p=#: 0%
