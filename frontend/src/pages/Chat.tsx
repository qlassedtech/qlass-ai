import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import ChatWindow from "../components/ChatWindow";
import { studentApi, type StudentProfile } from "../api";

type Context = { student: StudentProfile | null; setStudent: (s: StudentProfile) => void };

export default function Chat() {
  const { student, setStudent } = useOutletContext<Context>();
  const [balance, setBalance] = useState<number | null>(null);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>AI Tutor</h1>
          <p>Ask anything, send a photo of a question, record a voice note, or share a PDF — homework help, explanations, or a quick quiz</p>
        </div>
        {(balance ?? student?.credit_balance) !== undefined && (
          <p className="muted">₹{(balance ?? student?.credit_balance)?.toFixed(2)} credits</p>
        )}
      </div>
      <ChatWindow
        fetchHistory={studentApi.history}
        sendMessage={studentApi.sendMessage}
        onSendImage={studentApi.sendImage}
        onSendVoice={studentApi.sendVoice}
        onSendDocument={studentApi.sendDocument}
        onBalanceChange={(b) => {
          setBalance(b);
          if (student) setStudent({ ...student, credit_balance: b });
        }}
      />
    </div>
  );
}
