import { useEffect, useState } from "react";
import { api, type SchoolOverview, type Student, type TeacherAccount } from "../api";

export default function Schools() {
  const [schools, setSchools] = useState<SchoolOverview[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [expandedSchoolId, setExpandedSchoolId] = useState<number | null>(null);
  const [allStudents, setAllStudents] = useState<Student[]>([]);
  const [allTeachers, setAllTeachers] = useState<TeacherAccount[]>([]);
  const [selectedStudentIds, setSelectedStudentIds] = useState<Set<number>>(new Set());
  const [selectedTeacherIds, setSelectedTeacherIds] = useState<Set<number>>(new Set());
  const [trialDuration, setTrialDuration] = useState("30");
  const [studentPilotCredits, setStudentPilotCredits] = useState("50");
  const [teacherToolCredits, setTeacherToolCredits] = useState("500");
  const [trialStatus, setTrialStatus] = useState<string | null>(null);
  const [trialLoading, setTrialLoading] = useState(false);

  function load() {
    api.getSchoolsOverview().then(setSchools).catch((err) => setError(err instanceof Error ? err.message : "Failed to load"));
  }

  useEffect(load, []);

  async function handleStatusChange(id: number, sales_status: string) {
    try {
      await api.updateSchoolSales(id, { sales_status });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update status");
    }
  }

  async function handleSaveNotes(id: number) {
    try {
      await api.updateSchoolSales(id, { sales_notes: notes[id] });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save notes");
    }
  }

  async function toggleGrantTrialPanel(schoolId: number) {
    if (expandedSchoolId === schoolId) {
      setExpandedSchoolId(null);
      return;
    }
    setExpandedSchoolId(schoolId);
    setSelectedStudentIds(new Set());
    setSelectedTeacherIds(new Set());
    setTrialStatus(null);
    // listStudents/listTeachers return everyone across every school for
    // super_admin — filtered client-side to just this one below, since
    // there's no per-centre query param on those endpoints today.
    const [students, teachers] = await Promise.all([api.fetchAllStudents(), api.listTeachers()]);
    setAllStudents(students.filter((s) => s.centre_id === schoolId));
    setAllTeachers(teachers.filter((t) => t.centre_id === schoolId));
  }

  function toggleStudent(id: number) {
    setSelectedStudentIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleTeacher(id: number) {
    setSelectedTeacherIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleGrantTrial(schoolId: number) {
    if (selectedStudentIds.size === 0 && selectedTeacherIds.size === 0) {
      setTrialStatus("Select at least one student or teacher.");
      return;
    }
    setTrialLoading(true);
    setTrialStatus(null);
    try {
      const messages: string[] = [];
      if (selectedStudentIds.size) {
        const result = await api.launchSchoolPilot(schoolId, {
          duration_days: Number(trialDuration),
          credits_per_student: Number(studentPilotCredits),
          teacher_tool_credits: Number(teacherToolCredits),
          student_ids: [...selectedStudentIds],
        });
        messages.push(`Student pilot: ₹${result.credits_per_student} credited to ${result.granted_count} student(s); ₹${result.teacher_tool_credits} shared for teacher tools.`);
      }
      if (selectedTeacherIds.size) {
        const result = await api.activateSchoolTrialSubscriptions(schoolId, {
          duration_days: Number(trialDuration), teacher_ids: [...selectedTeacherIds],
        });
        messages.push(`Teacher access: ${result.activated_count} profile(s) activated.`);
      }
      setTrialStatus(messages.join(" "));
      setSelectedStudentIds(new Set());
      setSelectedTeacherIds(new Set());
    } catch (err) {
      setTrialStatus(err instanceof Error ? err.message : "Failed to grant trial");
    } finally {
      setTrialLoading(false);
    }
  }

  if (error) return <p className="error">{error}</p>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Schools & Sales Pipeline</h1>
          <p>Every school on Qlass, with churn risk flagged from real usage</p>
        </div>
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>School</th>
              <th>Students</th>
              <th>Credit Balance</th>
              <th>Last Activity</th>
              <th>Status</th>
              <th>Notes</th>
              <th>Trial Access</th>
            </tr>
          </thead>
          <tbody>
            {schools.map((s) => (
              <tr key={s.id} style={s.is_churn_risk ? { background: "var(--error-tint)" } : undefined}>
                <td>
                  {s.name}
                  {s.is_churn_risk && (
                    <span className="muted" style={{ display: "block", fontSize: 12, color: "var(--error)" }}>
                      ⚠ Churn risk — inactive {s.days_inactive ?? "many"} days
                    </span>
                  )}
                </td>
                <td>{s.student_count}</td>
                <td>₹{s.school_credit_balance.toFixed(2)}</td>
                <td>{s.last_activity ? new Date(s.last_activity).toLocaleDateString() : "Never"}</td>
                <td>
                  <select value={s.sales_status} onChange={(e) => handleStatusChange(s.id, e.target.value)}>
                    <option value="prospect">Prospect</option>
                    <option value="trial">Trial</option>
                    <option value="active">Active</option>
                    <option value="churned">Churned</option>
                  </select>
                </td>
                <td>
                  <div className="inline-form">
                    <input
                      placeholder={s.sales_notes || "Add a note..."}
                      value={notes[s.id] ?? s.sales_notes ?? ""}
                      onChange={(e) => setNotes((prev) => ({ ...prev, [s.id]: e.target.value }))}
                    />
                    <button type="button" onClick={() => handleSaveNotes(s.id)}>
                      Save
                    </button>
                  </div>
                </td>
                <td>
                  <button type="button" onClick={() => toggleGrantTrialPanel(s.id)}>
                    {expandedSchoolId === s.id ? "Close" : "Grant Trial"}
                  </button>
                </td>
              </tr>
            ))}
            {schools.map(
              (s) =>
                expandedSchoolId === s.id && (
                  <tr key={`${s.id}-trial-panel`}>
                    <td colSpan={7}>
                      <div className="card" style={{ boxShadow: "none" }}>
                        <h3>Grant Free Trial — {s.name}</h3>
                        <p className="muted" style={{ marginBottom: 12 }}>
                          Default launch: up to 100 learners, 7–45 days, ₹50/student and ₹500 shared teacher-tool credits.
                          Learners receive text, voice, image-question, document and video support; image generation stays off during the pilot.
                        </p>
                        <label style={{ display: "inline-block", marginBottom: 12 }}>
                          Duration (days)
                          <input
                            type="number"
                            min="7"
                            max="45"
                            value={trialDuration}
                            onChange={(e) => setTrialDuration(e.target.value)}
                            style={{ width: 100, marginLeft: 8 }}
                          />
                        </label>
                        <label style={{ display: "inline-block", marginBottom: 12, marginLeft: 16 }}>
                          Shared teacher-tool credits (₹)
                          <input
                            type="number"
                            min="0"
                            max="500"
                            step="1"
                            value={teacherToolCredits}
                            onChange={(e) => setTeacherToolCredits(e.target.value)}
                            style={{ width: 100, marginLeft: 8 }}
                          />
                        </label>
                        <label style={{ display: "inline-block", marginBottom: 12, marginLeft: 16 }}>
                          Student credits each (₹)
                          <input
                            type="number"
                            min="1"
                            step="1"
                            value={studentPilotCredits}
                            onChange={(e) => setStudentPilotCredits(e.target.value)}
                            style={{ width: 100, marginLeft: 8 }}
                          />
                        </label>

                        <div className="grid-2">
                          <div>
                            <p className="muted" style={{ marginBottom: 6 }}>Students — wallet-credit pilot</p>
                            {allStudents.length === 0 && <p className="muted">No students at this school yet.</p>}
                            {allStudents.map((student) => (
                              <label key={student.id} className="toggle-row">
                                <input
                                  type="checkbox"
                                  checked={selectedStudentIds.has(student.id)}
                                  onChange={() => toggleStudent(student.id)}
                                />
                                {student.name} ({student.phone})
                              </label>
                            ))}
                          </div>
                          <div>
                            <p className="muted" style={{ marginBottom: 6 }}>Teachers — optional personal "My AI Tutor" access</p>
                            <p className="muted">All school teachers can use the shared pilot pool for worksheets, quizzes and presentations.</p>
                            {allTeachers.length === 0 && <p className="muted">No teachers at this school yet.</p>}
                            {allTeachers.map((t) => (
                              <label key={t.id} className="toggle-row">
                                <input
                                  type="checkbox"
                                  checked={selectedTeacherIds.has(t.id)}
                                  onChange={() => toggleTeacher(t.id)}
                                />
                                {t.name} ({t.phone})
                              </label>
                            ))}
                          </div>
                        </div>

                        <button
                          type="button"
                          style={{ marginTop: 16 }}
                          onClick={() => handleGrantTrial(s.id)}
                          disabled={trialLoading}
                        >
                          {trialLoading ? "Granting…" : "Grant Trial"}
                        </button>
                        {trialStatus && <p className="status" style={{ marginTop: 8 }}>{trialStatus}</p>}
                      </div>
                    </td>
                  </tr>
                ),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
