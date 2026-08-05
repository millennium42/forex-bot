"use client";

import React, { useState, useEffect } from "react";
import { 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  Area,
  AreaChart
} from "recharts";
import { 
  TrendingUp, 
  TrendingDown, 
  Activity, 
  ShieldCheck, 
  Clock, 
  AlertCircle,
  BarChart2,
  DollarSign,
  ArrowUpRight,
  ArrowDownRight
} from "lucide-react";

// Mock data for demonstration purposes
const equityData = [
  { time: "09:00", equity: 100000 },
  { time: "10:00", equity: 100150 },
  { time: "11:00", equity: 100080 },
  { time: "12:00", equity: 100400 },
  { time: "13:00", equity: 100350 },
  { time: "14:00", equity: 100800 },
  { time: "15:00", equity: 101200 },
];

const activeTrades = [
  { id: "T-1042", pair: "EURUSD", side: "BUY", entry: 1.0924, current: 1.0945, pnl: 210, time: "10:23 AM" },
  { id: "T-1043", pair: "GBPUSD", side: "SELL", entry: 1.2650, current: 1.2630, pnl: 200, time: "11:45 AM" },
  { id: "T-1044", pair: "USDJPY", side: "BUY", entry: 149.20, current: 149.10, pnl: -100, time: "13:10 PM" },
];

const recentSignals = [
  { id: "S-889", pair: "AUDUSD", direction: "BUY", confidence: 0.85, technique: "Fusion (TA+NLP)", time: "14:30 PM" },
  { id: "S-890", pair: "USDCAD", direction: "SELL", confidence: 0.72, technique: "TA Only", time: "14:45 PM" },
  { id: "S-891", pair: "EURGBP", direction: "HOLD", confidence: 0.40, technique: "Conflict", time: "15:00 PM" },
];

const promotionGates = [
  { name: "Win Rate ≥ 55%", value: "58.4%", passed: true },
  { name: "Sharpe ≥ 1.0", value: "1.24", passed: true },
  { name: "Max Drawdown ≤ 10%", value: "4.2%", passed: true },
  { name: "Profit Factor ≥ 1.3", value: "1.45", passed: true },
  { name: "Backtest Deviation < 15%", value: "8.1%", passed: true },
];

export default function Dashboard() {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMounted(true);
  }, []);

  if (!mounted) return null;

  return (
    <div className="min-h-screen p-6 md:p-8 max-w-7xl mx-auto animate-fade-in">
      
      {/* Header */}
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center mb-8 gap-4 animate-slide-up" style={{ animationDelay: "0.1s" }}>
        <div>
          <h1 className="text-3xl md:text-4xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-purple-500">
            Forex Bot
          </h1>
          <p className="text-slate-400 mt-1">Intelligent Agentic Trading Dashboard</p>
        </div>
        <div className="flex items-center gap-3 bg-slate-800/50 py-2 px-4 rounded-full border border-slate-700/50 backdrop-blur-md">
          <div className="h-2.5 w-2.5 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)] animate-pulse"></div>
          <span className="text-sm font-medium text-emerald-400">System Online (Demo Mode)</span>
        </div>
      </header>

      {/* Overview Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8 animate-slide-up" style={{ animationDelay: "0.2s" }}>
        <MetricCard title="Total Equity" value="$101,200.00" change="+1.2%" isPositive={true} icon={<DollarSign className="w-5 h-5 text-blue-400" />} />
        <MetricCard title="Today's P&L" value="$1,200.00" change="+1.2%" isPositive={true} icon={<Activity className="w-5 h-5 text-emerald-400" />} />
        <MetricCard title="Open Positions" value="3" subtitle="Total Exposure: 1.5%" icon={<BarChart2 className="w-5 h-5 text-purple-400" />} />
        <MetricCard title="Promotion Status" value="5 / 5" subtitle="Ready for evaluation" icon={<ShieldCheck className="w-5 h-5 text-amber-400" />} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
        
        {/* Equity Curve Chart */}
        <div className="lg:col-span-2 glass-card rounded-2xl p-6 animate-slide-up" style={{ animationDelay: "0.3s" }}>
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-xl font-semibold flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-blue-400" />
              Equity Curve (Intraday)
            </h2>
            <select className="bg-slate-800/80 border border-slate-700 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-500">
              <option>Today</option>
              <option>This Week</option>
              <option>This Month</option>
            </select>
          </div>
          <div className="h-[300px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equityData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                <XAxis dataKey="time" stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} />
                <YAxis domain={['dataMin - 200', 'dataMax + 200']} stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} tickFormatter={(val) => `$${val.toLocaleString()}`} />
                <Tooltip 
                  contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', borderColor: 'rgba(51, 65, 85, 0.5)', borderRadius: '8px', color: '#fff' }}
                  itemStyle={{ color: '#3b82f6' }}
                />
                <Area type="monotone" dataKey="equity" stroke="#3b82f6" strokeWidth={3} fillOpacity={1} fill="url(#colorEquity)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Promotion Gates */}
        <div className="glass-card rounded-2xl p-6 animate-slide-up" style={{ animationDelay: "0.4s" }}>
          <h2 className="text-xl font-semibold mb-6 flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-purple-400" />
            Promotion Gates
          </h2>
          <div className="space-y-4">
            {promotionGates.map((gate, idx) => (
              <div key={idx} className="flex justify-between items-center p-3 rounded-xl bg-slate-800/40 border border-slate-700/30">
                <div className="flex items-center gap-3">
                  <div className={`p-1.5 rounded-full ${gate.passed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'}`}>
                    {gate.passed ? <ShieldCheck className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                  </div>
                  <span className="text-sm font-medium text-slate-200">{gate.name}</span>
                </div>
                <span className="text-sm font-bold text-slate-300">{gate.value}</span>
              </div>
            ))}
          </div>
          
          <button className="w-full mt-6 py-3 rounded-xl bg-gradient-to-r from-blue-600 to-purple-600 text-white font-medium shadow-lg hover:shadow-blue-500/25 transition-all active:scale-[0.98]">
            Evaluate Promotion
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 pb-8 animate-slide-up" style={{ animationDelay: "0.5s" }}>
        
        {/* Active Trades */}
        <div className="glass-card rounded-2xl p-6">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-xl font-semibold flex items-center gap-2">
              <Activity className="w-5 h-5 text-emerald-400" />
              Active Trades
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead className="text-xs text-slate-400 uppercase bg-slate-800/50 rounded-t-lg">
                <tr>
                  <th className="px-4 py-3 rounded-tl-lg">Pair</th>
                  <th className="px-4 py-3">Side</th>
                  <th className="px-4 py-3">Entry</th>
                  <th className="px-4 py-3">Current</th>
                  <th className="px-4 py-3 text-right rounded-tr-lg">P&L</th>
                </tr>
              </thead>
              <tbody>
                {activeTrades.map((trade, idx) => (
                  <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-3 font-medium text-slate-200">{trade.pair}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-1 rounded text-xs font-semibold ${trade.side === 'BUY' ? 'bg-blue-500/20 text-blue-400' : 'bg-rose-500/20 text-rose-400'}`}>
                        {trade.side}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-400">{trade.entry.toFixed(4)}</td>
                    <td className="px-4 py-3 text-slate-300">{trade.current.toFixed(4)}</td>
                    <td className={`px-4 py-3 text-right font-semibold flex justify-end items-center gap-1 ${trade.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {trade.pnl >= 0 ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                      ${Math.abs(trade.pnl).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Recent Signals */}
        <div className="glass-card rounded-2xl p-6">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-xl font-semibold flex items-center gap-2">
              <Clock className="w-5 h-5 text-amber-400" />
              Recent Signals
            </h2>
          </div>
          <div className="space-y-3">
            {recentSignals.map((signal, idx) => (
              <div key={idx} className="flex items-center justify-between p-4 rounded-xl bg-slate-800/40 border border-slate-700/30 hover:bg-slate-800/60 transition-colors">
                <div className="flex items-center gap-4">
                  <div className={`flex items-center justify-center w-10 h-10 rounded-lg ${
                    signal.direction === 'BUY' ? 'bg-blue-500/20 text-blue-400' : 
                    signal.direction === 'SELL' ? 'bg-rose-500/20 text-rose-400' : 
                    'bg-slate-500/20 text-slate-400'
                  }`}>
                    {signal.direction === 'BUY' ? <TrendingUp className="w-5 h-5" /> : 
                     signal.direction === 'SELL' ? <TrendingDown className="w-5 h-5" /> : 
                     <Activity className="w-5 h-5" />}
                  </div>
                  <div>
                    <h3 className="font-semibold text-slate-200">{signal.pair}</h3>
                    <p className="text-xs text-slate-400">{signal.technique} • {signal.time}</p>
                  </div>
                </div>
                <div className="text-right">
                  <span className={`text-sm font-bold block ${
                    signal.direction === 'BUY' ? 'text-blue-400' : 
                    signal.direction === 'SELL' ? 'text-rose-400' : 
                    'text-slate-400'
                  }`}>{signal.direction}</span>
                  <span className="text-xs text-slate-400 mt-0.5 block">Conf: {(signal.confidence * 100).toFixed(0)}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}

// Helper Component for Metrics
function MetricCard({ title, value, change, isPositive, subtitle, icon }: { title: string, value: string, change?: string, isPositive?: boolean, subtitle?: string, icon: React.ReactNode }) {
  return (
    <div className="glass-card rounded-2xl p-5 relative overflow-hidden group hover:border-slate-600/50 transition-all">
      <div className="absolute top-0 right-0 p-4 opacity-20 group-hover:opacity-40 transition-opacity transform group-hover:scale-110 group-hover:rotate-12 duration-300">
        {icon}
      </div>
      <div className="flex items-center gap-2 mb-3">
        <div className="p-2 bg-slate-800/80 rounded-lg shadow-sm border border-slate-700/50">
          {icon}
        </div>
        <h3 className="text-sm font-medium text-slate-400">{title}</h3>
      </div>
      <div className="flex items-end justify-between">
        <h4 className="text-2xl font-bold text-slate-100">{value}</h4>
        {change && (
          <span className={`text-sm font-semibold flex items-center mb-1 ${isPositive ? 'text-emerald-400' : 'text-rose-400'}`}>
            {isPositive ? <ArrowUpRight className="w-3.5 h-3.5 mr-0.5" /> : <ArrowDownRight className="w-3.5 h-3.5 mr-0.5" />}
            {change}
          </span>
        )}
      </div>
      {subtitle && <p className="text-xs text-slate-500 mt-2">{subtitle}</p>}
    </div>
  );
}
