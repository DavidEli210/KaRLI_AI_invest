# KaRLI - AI Trading Agent 🚀💸

Welcome to the **KaRLI_AI** project! This platform leverages **Large Language Models (LLMs)** and the **Model Context Protocol (MCP)** to create an **autonomous investment portfolio management system**. It aims to discover and act on market opportunities using real-time data and intelligent decision-making, providing smart, automated investment strategies. 🌟

---

## 📖 Overview

An agentic AI system built on LangGraph, that pulls market data using mcp server tools, manages capital allocation, and executes live trades through Alpaca's API — all triggered automatically by a scheduled AWS Lambda function.

Key features include:
- Autonomous trading decisions: `Buy` and `Sell` instructions based on live data 📈📉
- Secure user registration and authentication via **Amazon Cognito** 👤
- Real-time portfolio analysis with visual insights on the React frontend 📊
- Connection to live trading accounts via Alpaca API 🔗
- Periodic, hands-free portfolio evaluation driven by AWS Lambda 🤖

---

## ✨ Features

1. **User Management & Registration** 👤
   - Secure sign-up, login, and user registration managed completely by **Amazon Cognito**.
   - Input your Alpaca broker credentials to link your investment account.

2. **Portfolio Summary & Insights** 💼
   - Total portfolio worth, account balance, and existing holdings displayed dynamically.
   - Historical trading actions and performance metrics via a modern React UI.

3. **Autonomous AI Trading Agent** 🧠
   - Intelligent decision-making using **LangChain**, **LangGraph**, and **Anthropic's Claude**.
   - Access to real-time market data, technical indicators (RSI, MACD, SMA), and financial news through the **Alpha Vantage MCP server**.
   - Strict capital preservation and risk management rules enforced within the prompt strategy.

4. **Automated Trading Execution** 🤖
   - Direct integration with **Alpaca API** for seamless trade execution.
   - A scheduled **AWS Lambda** workflow that triggers trading analysis periodically for all registered users without manual intervention.

---

## 🛠️ Technologies Used

### Backend 🖥️
- **Python (Flask)**: Web server exposing API endpoints for the trading logic and frontend integration.
- **LangChain & LangGraph**: AI orchestration and tool mapping.
- **Model Context Protocol (MCP)**: Connecting Claude to Alpha Vantage for live market data.
- **Alpaca API**: Executing market trades and fetching portfolio data.

### Frontend 🌐
- **React.js & Vite**: Modern, lightning-fast UI library and build tool.
- **Tailwind CSS & Shadcn UI**: For elegant, responsive, and accessible styling.
- **Recharts**: For dynamic and responsive financial visualizations.

### Cloud & Automation ☁️
- **AWS Lambda**: Scheduled chron jobs triggering the AI trading workflow.
- **Amazon Cognito**: User directories, registration, and authentication.
- **AWS S3**: Static website hosting for the frontend.
- **AWS CloudFront**: CDN for the frontend.
- **AWS ECS**: Running the backend and open for scaling .
---

## 🚀 How It Works

1. **User Registration & Setup**
   - Users register via the platform utilizing **Amazon Cognito**. 
   - After signing in, users provide their Alpaca API credentials to enable live agentic trading.

2. **Scheduled Trigger**
   - At scheduled intervals, the AWS Lambda function fetches all registered users from Cognito.
   - It invokes the trading backend endpoint for each user.

3. **Data Processing & AI Strategy**
   - The Flask backend initiates the LangGraph trading workflow.
   - The AI agent dynamically analyzes the user's Alpaca portfolio.
   - It issues MCP tool calls to retrieve technical indicators and live quotes to find new opportunities.

4. **Automated Trading**
   - LLM outputs a structured JSON list of validated trade instructions.
   - The backend directly submits these instructions to the Alpaca broker API.

