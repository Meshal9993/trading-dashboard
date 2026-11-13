# IST 402 — Assignment 4, Part 4.1
## Live Market Workstation: Market Data and Dashboard

**Student:** Meshal Alqufaidi

**GitHub Repository:** https://github.com/Meshal9993/trading-dashboard

**Branch:** main

**Submission Commit SHA:

### 1. Project Overview

In this assignment, I created a market dashboard that visualizes AAPL stock prices. The application is built with Vue 3 frontend, FastAPI backend, and SQLite database to store market data.

The dashboard connects to Yahoo Finance through a WebSocket and shows candlestick charts, trading volumes when available, and information about the source of data. The user can also see saved market data if there is no live data feed.

### 2. Completed Features

I have implemented the following features:

- Connected the backend to Yahoo Finance through a WebSocket.
- Visualized AAPL candlestick charts and trading volumes when available.
- Added five timeframes: 1 min, 5 mins, 15 mins, 1 hour, and 1 day.
- Used WebSockets to send market updates from FastAPI to Vue.
- Stored market observations in SQLite database.
- Added source timestamps, receipt timestamps, and feed status.
- Added a 30 second fallback polling if the Yahoo WebSocket becomes unavailable.
- Created a Replay feature to visualize recorded market observations.
- Added handling for duplicate messages, revisions, and late observations.

### 3. Design Decisions

I used Vue 3 as it allows creating an interactive dashboard. FastAPI is responsible for the market data and sending updates to the frontend. SQLite database is used to store observations to reuse them later.

One of the design decisions was to store incoming observations before sending updates to the dashboard. This allows keeping a record of the original market data.

The application uses source timestamps to calculate candles. For Replay, it uses the original order of observations' arrival. This allows seeing how candles were changed due to new or revised data.

I also added a fallback connection and clear feed status messages to inform the user about the unavailability or delay of the data.

### 4. Testing and Verification

I tested the backend, frontend, database, and dashboard.

**Final test results:**

- Backend: 182 tests passed.
- Frontend: 40 tests passed.
- Production build: passed.

I verified that the real Yahoo Finance update was processed by the backend, stored in SQLite database, sent through WebSocket, and appeared on the visible chart.

I simulated a Yahoo Finance feed outage during testing and verified recovery. The application kept showing saved data during the simulated outage.

For Replay, I tested 69 saved real market observations twice: both tests gave the same result without changing the stored records.

I also tested duplicate observations, revisions, late arrivals, and various chart timeframes.

### 5. Limitations

Candle aggregation focuses on AAPL regular market sessions. Saved history and replay are available outside market hours.

The data from Yahoo Finance might be delayed, and the application does not guarantee real-time exchange prices. Some volume values are unknown as the available market observations do not provide enough information to calculate them correctly.

Replay works with a constant playback speed. The application is a learning project and is not intended for real trading.

### 6. Supporting Documentation and Evidence

- [README](https://github.com/Meshal9993/trading-dashboard/blob/main/README.md)
- [Design Pipeline](https://github.com/Meshal9993/trading-dashboard/blob/main/docs/design-pipeline.md)
- [Verification Results](https://github.com/Meshal9993/trading-dashboard/blob/main/docs/verification.md)
- [Live Streaming Evidence](https://github.com/Meshal9993/trading-dashboard/tree/main/docs/evidence/part4-1/prompt08)
- [Replay and Final Test Evidence](https://github.com/Meshal9993/trading-dashboard/tree/main/docs/evidence/part4-1/prompt09)

### 7. Demonstration Video

**Duration:** 53.7 seconds

[Watch the Application Demonstration](https://github.com/Meshal9993/trading-dashboard/blob/main/docs/evidence/part4-1/final-demo.mov)

The video shows the dashboard, candlestick chart, market data status, timeframe selection, and Replay feature.

### 8. Conclusion

This project allowed me to learn how market information flows from an external source to backend, database, and frontend dashboard. I also learned how to handle connection failures, store market observations, and test the application based on recorded data.

