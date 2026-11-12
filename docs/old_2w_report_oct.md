Files: 20261004 to 20261020 (11 per coin) from `data/rec2w`

## SLOW (every 300 s): code rules only

- Entry checks while flat: 3,500
- Trades: 9 (2 wins, 7 losses)
- Net result after fees: **-69.97** on $10,000
- Average R: -0.33; worst trade -1.07R

| day (ET) | result |
|---|---|
| 2026-10-05 | -0.45 |
| 2026-10-06 | -38.34 |
| 2026-10-07 | -4.22 |
| 2026-10-09 | +6.28 |
| 2026-10-10 | +10.30 |
| 2026-10-13 | -16.91 |
| 2026-10-15 | -26.62 |

| opened (ET) | entry | stop | T1 | exit | minutes | $ | R |
|---|---|---|---|---|---|---|---|
| Mon 10-05 17:20 | 184.79 | 182.47 | 188.63 | BTC risk-off: BTC data stale | 56.4 | -0.45 | -0.02 |
| Tue 10-06 10:46 | 182.89 | 181.32 | 185.60 | stop | 43.5 | -25.34 | -1.07 |
| Tue 10-06 15:58 | 182.28 | 180.80 | 184.86 | BTC risk-off: BTC data stale | 15.2 | -13.00 | -0.56 |
| Wed 10-07 02:35 | 176.72 | 175.33 | 179.15 | BTC risk-off: BTC data stale | 74.0 | -4.22 | -0.35 |
| Fri 10-09 03:01 | 168.85 | 167.98 | 170.49 | stop | 4.0 | -9.84 | -1.04 |
| Fri 10-09 20:30 | 167.94 | 166.98 | 169.71 | T1: sold 50%; BTC risk-off: BTC data stale | 117.9 | +16.12 | +1.41 |
| Sat 10-10 21:16 | 168.75 | 167.52 | 170.93 | BTC risk-off: BTC data stale | 95.2 | +10.30 | +0.41 |
| Tue 10-13 15:15 | 162.12 | 159.61 | 166.20 | BTC risk-off: BTC data stale | 143.7 | -16.91 | -0.68 |
| Thu 10-15 18:54 | 158.41 | 157.62 | 159.91 | stop | 7.9 | -26.62 | -1.07 |

Why it said NO TRADE (share of entry checks, a check can have several):

- AAVE bids not stronger than asks for # min: 88%
- AAVE aggressive buying # < #: 64%
- AAVE #-min volume # median, needs #: 61%
- AAVE not in an uptrend: 53%
- AAVE price not accepting higher: 52%
- scheduled event: US data releases and stock open: 10%
- BTC data stale: 10%
- BTC not stable or rising: 4%
- BTC history incomplete: 1%
- BTC # in # min with accelerating selling: 0%
- stop # below entry: 0%

Runtime 129 s

## Data quality warning

- Recording span 401 h (Sun 10-04 to Wed 10-21); **130 h (32%) missing** — 485 gaps > 30 s.
  407 of them are 5–60 min (112.8 h), 5 are > 1 h (longest 7.7 h, 10-16). Same gaps on both coins,
  so the recorder itself stopped — most likely the Mac sleeping.
- 6 of 9 trades were force-closed by "BTC data stale" (no BTC update for > 10 s) when a gap hit.
  Those exits are recorder artefacts, not market signals, so this result does not yet judge SLOW.
- Fix before re-recording: run the recorder under `caffeinate -i` (keeps the Mac awake).
