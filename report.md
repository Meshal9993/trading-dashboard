# IST 402 — Assignment 4, Part 4.2
## Indicators, Simulated Trading, and Backtesting

**Student:** Meshal Alqufaidi

**GitHub Repository:** https://github.com/Meshal9993/trading-dashboard

**Branch:** part4-2

**Submission Commit SHA:** `10f6e7c40b5b380be202b034cb8309126a9cce6d`

### 1. Project Overview

For Part 4.2, I improved my trading dashboard by adding technical indicators, simulated trading, profit and loss calculations, and backtesting. The application uses Vue 3, FastAPI, and SQLite.

The goal was to understand how trading signals are calculated and how a simulated strategy performs using recorded market data.

### 2. Completed Features

**SMA Indicators**

I have included two Simple Moving Averages (SMA) with customizable time periods. Both SMA lines and their crossover points are shown on the dashboard.

Calculations are done with the help of completed candlesticks only. I have checked my calculations even when replaying the chart.

**Simulated Trading**

I introduced an account that allows players to trade without spending real cash through paper trading.

The software keeps track of the cash, stocks, orders, and transactions done through SQLite.

**Profit and Loss**

Calculations of profit made, profit not made, and total profit/loss have been incorporated.

The account balance and Profit & Loss information is presented in the dashboard. The calculation of gains/losses is based on the weighted average cost of purchase.
**Backtesting**

The backtest component I have developed backtests the SMA crossing strategy on recorded data.

This includes specifying the dates when testing occurs, starting balance, and the lengths of the SMAs. This component provides users with trading signals, completed trades, ending balance, and equity curve.
### 3. Design Decisions



In paper trading, orders are based on the next eligible price seen rather than making an assumption that the order is made at an earlier price.

Backtesting utilizes only data available at each decision point. This is to avoid using future data when generating signals for the application.

It is also separated from the usual simulated trading account to avoid any changes to stored account balances during testing.

### 4. Testing and Verification

I tested the indicators, paper trading, P&L calculations, and backtesting.

**Final results:**

- Backend: 223 tests passed.
- Frontend: 60 tests passed.
- Production build: passed.
- Browser smoke tests: passed.
- GitHub Actions: passed.

For the SMA calculations, I verified the instructor's example with upward crossover values of 16 and 13 and downward crossover values of 4 and 9.

For paper trading, I tested buying four shares at $100, selling one at $110, and closing three at $105. The account finished with $1,025 and zero shares.

For profit and loss, the final realized profit was $25.

For backtesting, I tested the SMA crossover strategy using a $1,000 starting balance. The application bought one share at $17 and sold it at $5. The final account equity was $988, with a $12 loss.

I also verified that repeated tests produced the same results and did not change the original paper trading account.

### 5. Limitations

The strategy currently implemented is based on AAPL, and operates with a hypothetical long-only trading account.

The strategy involves trading full shares with no trading costs or slippage. Orders may remain pending or be rejected. Successful simulated fills execute the full order quantity. One-share entries are applied to backtesting.

SMA, paper trading, P&L, and backtesting tests used constructed offline fixtures. No confirmation of real-time market delay and paper trading powered by Yahoo was provided.

Backtesting shows how the strategy performed on the selected historical data, but it does not guarantee future trading performance.

### 6. Supporting Documentation and Evidence

- [README](https://github.com/Meshal9993/trading-dashboard/blob/part4-2/README.md)
- [Design Pipeline](https://github.com/Meshal9993/trading-dashboard/blob/part4-2/docs/design-pipeline.md)
- [Verification Results](https://github.com/Meshal9993/trading-dashboard/blob/part4-2/docs/verification.md)
- [Part 4.2 Evidence](https://github.com/Meshal9993/trading-dashboard/tree/part4-2/docs/evidence/part4-2)
- [GitHub Actions Results](https://github.com/Meshal9993/trading-dashboard/actions/runs/44643421817)

### 7. Demonstration Video

[Watch the Part 4.2 Demonstration](https://github.com/Meshal9993/trading-dashboard/blob/part4-2/docs/evidence/part4-2/final-demo.mov)

The video shows the SMA indicators, paper trading controls, P&L history, and backtesting results.

The video is approximately 63 seconds long. Some filmed trade requests remained pending or were rejected, while separate automated tests verified the successful trade sequence and accounting results.

### 8. Conclusion

This project helped me understand how trading indicators work and how simulated trading can be tested. I learned how to calculate moving averages, record paper trades, measure profits and losses, and evaluate a strategy using historical market data.
