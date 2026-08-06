"use client";

import { Award, History, LayoutDashboard, Radio, Receipt } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Header } from "@/components/Header";
import { Tabs, type TabDef } from "@/components/Tabs";
import { KillSwitchModal } from "@/components/modals/KillSwitchModal";
import { SignalDetailModal } from "@/components/modals/SignalDetailModal";
import { TradeDetailModal } from "@/components/modals/TradeDetailModal";
import { AuditTab } from "@/components/tabs/AuditTab";
import { GatesTab } from "@/components/tabs/GatesTab";
import { OverviewTab } from "@/components/tabs/OverviewTab";
import { SignalsTab } from "@/components/tabs/SignalsTab";
import { TradesTab } from "@/components/tabs/TradesTab";
import {
  getAccountStatus,
  getAuditLog,
  getKillSwitchStatus,
  getOpenPositions,
  getPromotionStatus,
  getSignals,
  getTradeHistory,
  type AccountStatus,
  type AuditEntry,
  type KillSwitchStatus,
  type OpenPosition,
  type PromotionStatus,
  type SignalRecord,
  type TradeRecord,
} from "@/lib/api";
import { getPalette } from "@/lib/palette";
import { useTheme } from "@/lib/theme";

const TABS: TabDef[] = [
  { id: "overview", label: "Visão geral", icon: LayoutDashboard },
  { id: "trades", label: "Trades", icon: Receipt },
  { id: "signals", label: "Sinais", icon: Radio },
  { id: "gates", label: "Gates", icon: Award },
  { id: "audit", label: "Auditoria", icon: History },
];

const REFRESH_INTERVAL_MS = 5000;

const DISCONNECTED_ACCOUNT: AccountStatus = {
  balance: null,
  equity: null,
  currency: null,
  connected: false,
  demo: null,
  detail: null,
};

export default function Dashboard() {
  const { theme } = useTheme();
  const palette = getPalette(theme);

  const [activeTab, setActiveTab] = useState("overview");
  const [account, setAccount] = useState<AccountStatus>(DISCONNECTED_ACCOUNT);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [signals, setSignals] = useState<SignalRecord[]>([]);
  const [openPositions, setOpenPositions] = useState<OpenPosition[]>([]);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [promotion, setPromotion] = useState<PromotionStatus | null>(null);
  const [killSwitch, setKillSwitch] = useState<KillSwitchStatus>({ active: false });

  const [selectedTrade, setSelectedTrade] = useState<TradeRecord | null>(null);
  const [selectedSignal, setSelectedSignal] = useState<SignalRecord | null>(null);
  const [showKillSwitchModal, setShowKillSwitchModal] = useState(false);

  const fetchAll = useCallback(async () => {
    const results = await Promise.allSettled([
      getAccountStatus(),
      getTradeHistory({ limit: 200 }),
      getSignals(100),
      getAuditLog(undefined, 100),
      getPromotionStatus(),
      getKillSwitchStatus(),
      getOpenPositions(),
    ]);

    if (results[0].status === "fulfilled") setAccount(results[0].value);
    else setAccount(DISCONNECTED_ACCOUNT);

    if (results[1].status === "fulfilled") setTrades(results[1].value);
    if (results[2].status === "fulfilled") setSignals(results[2].value);
    if (results[3].status === "fulfilled") setAuditEntries(results[3].value);
    if (results[4].status === "fulfilled") setPromotion(results[4].value);
    if (results[5].status === "fulfilled") setKillSwitch(results[5].value);
    if (results[6].status === "fulfilled") setOpenPositions(results[6].value);
    else setOpenPositions([]);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- carga inicial de dados ao vivo
    fetchAll();
    const interval = setInterval(fetchAll, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchAll]);

  return (
    <div className="min-h-screen p-6 md:p-8 max-w-7xl mx-auto animate-fade-in">
      <Header account={account} />

      <Tabs tabs={TABS} active={activeTab} onChange={setActiveTab} />

      {activeTab === "overview" && (
        <OverviewTab
          account={account}
          trades={trades}
          signals={signals}
          openPositions={openPositions}
          auditEntries={auditEntries}
          promotion={promotion}
          killSwitchActive={killSwitch.active}
          palette={palette}
          onOpenKillSwitch={() => setShowKillSwitchModal(true)}
        />
      )}
      {activeTab === "trades" && (
        <TradesTab trades={trades} currency={account.currency} onSelectTrade={setSelectedTrade} />
      )}
      {activeTab === "signals" && <SignalsTab signals={signals} onSelectSignal={setSelectedSignal} />}
      {activeTab === "gates" && <GatesTab promotion={promotion} />}
      {activeTab === "audit" && <AuditTab entries={auditEntries} />}

      {selectedTrade && (
        <TradeDetailModal trade={selectedTrade} currency={account.currency} onClose={() => setSelectedTrade(null)} />
      )}
      {selectedSignal && <SignalDetailModal signal={selectedSignal} onClose={() => setSelectedSignal(null)} />}
      {showKillSwitchModal && (
        <KillSwitchModal
          active={killSwitch.active}
          onClose={() => setShowKillSwitchModal(false)}
          onDone={setKillSwitch}
        />
      )}
    </div>
  );
}
