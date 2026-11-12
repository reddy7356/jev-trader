Files: 20261023 to 20261112 (24 per coin) from `data/rec`

## SLOW (every 300 s): code rules only

- Entry checks while flat: 3,725
- Trades: 23 (10 wins, 13 losses)
- Net result after fees: **-12.35** on $10,000
- Average R: -0.00; worst trade -1.16R

| day (ET) | result |
|---|---|
| 2026-10-24 | -25.46 |
| 2026-10-27 | -31.60 |
| 2026-10-29 | +55.92 |
| 2026-10-30 | +5.60 |
| 2026-10-31 | +53.65 |
| 2026-11-01 | +2.68 |
| 2026-11-04 | +23.02 |
| 2026-11-07 | -29.31 |
| 2026-11-08 | -24.39 |
| 2026-11-09 | -36.19 |
| 2026-11-10 | -23.60 |
| 2026-11-11 | +17.33 |

| opened (ET) | entry | stop | T1 | exit | minutes | $ | R |
|---|---|---|---|---|---|---|---|
| Fri 10-23 22:11 | 144.60 | 143.13 | 147.08 | stop | 192.7 | -25.46 | -1.02 |
| Tue 10-27 16:17 | 132.51 | 131.79 | 133.85 | stop | 67.6 | -23.57 | -1.16 |
| Tue 10-27 17:35 | 133.88 | 131.39 | 137.89 | BTC risk-off: BTC data stale | 95.1 | -8.03 | -0.32 |
| Thu 10-29 06:37 | 131.83 | 130.28 | 134.42 | T1: sold 50%; T2 | 302.0 | +55.92 | +2.25 |
| Fri 10-30 10:45 | 125.88 | 122.90 | 130.60 | time stop: no T1 within 240 min | 240.0 | -5.82 | -0.23 |
| Fri 10-30 17:41 | 128.04 | 126.53 | 130.56 | BTC risk-off: BTC data stale | 139.0 | +11.42 | +0.46 |
| Fri 10-30 20:30 | 130.71 | 128.37 | 134.48 | time stop: no T1 within 240 min | 240.0 | -8.02 | -0.32 |
| Sat 10-31 02:05 | 131.47 | 129.01 | 135.41 | BTC risk-off: BTC data stale | 209.2 | +2.51 | +0.10 |
| Sat 10-31 06:44 | 133.20 | 131.41 | 136.14 | BTC risk-off: BTC data stale | 98.4 | +11.53 | +0.46 |
| Sat 10-31 18:03 | 134.06 | 133.08 | 135.80 | time stop: no T1 within 240 min | 240.0 | +9.72 | +0.39 |
| Sat 10-31 22:08 | 134.76 | 133.88 | 136.35 | T1: sold 50%; T2 | 73.9 | +37.91 | +2.25 |
| Sun 11-01 00:52 | 139.37 | 136.10 | 144.54 | time stop: no T1 within 240 min | 240.0 | -14.48 | -0.58 |
| Sun 11-01 19:28 | 134.77 | 133.89 | 136.35 | T1: sold 50%; stop at entry after T1 | 145.2 | +17.16 | +0.68 |
| Wed 11-04 01:46 | 129.72 | 128.47 | 131.86 | T1: sold 50%; BTC risk-off: BTC data stale | 372.6 | +23.02 | +0.92 |
| Sat 11-07 01:22 | 131.68 | 130.44 | 133.80 | BTC risk-off: BTC data stale | 117.0 | -5.07 | -0.20 |
| Sat 11-07 22:50 | 132.08 | 130.89 | 134.12 | stop | 51.5 | -24.24 | -1.05 |
| Sun 11-08 11:07 | 129.77 | 129.01 | 131.16 | stop | 38.9 | -25.43 | -1.01 |
| Sun 11-08 12:51 | 129.00 | 128.20 | 130.45 | T1: sold 50%; stop at entry after T1 | 167.5 | +17.04 | +0.68 |
| Sun 11-08 18:14 | 127.78 | 124.87 | 132.40 | BTC risk-off: BTC data stale | 45.9 | -16.00 | -0.64 |
| Mon 11-09 07:30 | 133.41 | 130.51 | 138.02 | time stop: no T1 within 240 min | 240.0 | -10.74 | -0.43 |
| Mon 11-09 12:56 | 133.31 | 132.27 | 135.13 | stop | 155.7 | -25.45 | -1.02 |
| Tue 11-10 17:14 | 124.82 | 123.04 | 127.74 | time stop: no T1 within 240 min | 240.0 | -23.60 | -0.95 |
| Wed 11-11 03:20 | 123.36 | 122.44 | 124.98 | T1: sold 50%; stop at entry after T1 | 187.7 | +17.33 | +0.70 |

Why it said NO TRADE (share of entry checks, a check can have several):

- AAVE bids not stronger than asks for # min: 81%
- AAVE #-min volume # median, needs #: 68%
- AAVE aggressive buying # < #: 62%
- AAVE not in an uptrend: 56%
- AAVE price not accepting higher: 52%
- scheduled event: US data releases and stock open: 9%
- BTC not stable or rising: 7%
- BTC history incomplete: 1%
- scheduled event: FOMC decision and press conference: 1%
- BTC # in # min: 0%
- BTC data stale: 0%
- BTC # in # min with accelerating selling: 0%

Runtime 4486 s
