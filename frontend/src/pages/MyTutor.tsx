import { useEffect, useState } from "react";
import ChatWindow from "../components/ChatWindow";
import { api } from "../api";

export default function MyTutor() {
  const [balance, setBalance] = useState<number | null>(null);

  useEffect(() => {
    api.getMyTutor().then((t) => setBalance(t.credit_balance));
  }, []);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>My AI Tutor</h1>
          <p>Your own personal tutor account, separate from any student's</p>
        </div>
        {balance !== null && <p className="muted">₹{balance.toFixed(2)} credits</p>}
      </div>
      <ChatWindow
        fetchHistory={api.getMyTutorHistory}
        sendMessage={api.sendMyTutorMessage}
        onBalanceChange={setBalance}
      />
    </div>
  );
}
