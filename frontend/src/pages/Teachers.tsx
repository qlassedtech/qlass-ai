import { useEffect, useRef, useState } from "react";
import { api, absoluteUrl, type TeacherAccount } from "../api";

export default function Teachers() {
  const [teachers, setTeachers] = useState<TeacherAccount[]>([]);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("teacher");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const fileInputRefs = useRef<Record<number, HTMLInputElement | null>>({});

  function load() {
    api.listTeachers().then(setTeachers);
  }

  useEffect(load, []);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setStatus(null);
    try {
      await api.createTeacher({ name, phone, password, role });
      setName("");
      setPhone("");
      setPassword("");
      setRole("teacher");
      setStatus("Account created!");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add teacher");
    }
  }

  async function handlePhotoChange(teacherId: number, e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    await api.uploadTeacherPhoto(teacherId, file);
    load();
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Teacher Accounts</h1>
          <p>Manage who has portal access at your school</p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 20 }}>
        <h3>Add Teacher</h3>
        <form onSubmit={handleAdd} className="inline-form">
          <input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="WhatsApp number" value={phone} onChange={(e) => setPhone(e.target.value)} required />
          <input
            type="password"
            placeholder="Temporary password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
          />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="teacher">Teacher</option>
            <option value="admin">Admin</option>
          </select>
          <button type="submit">Add</button>
        </form>
        {status && <p className="status">{status}</p>}
        {error && <p className="error">{error}</p>}
      </div>

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Photo</th>
              <th>Name</th>
              <th>Phone</th>
              <th>Role</th>
            </tr>
          </thead>
          <tbody>
            {teachers.map((t) => (
              <tr key={t.id}>
                <td>
                  <div
                    className="photo-preview"
                    style={{ width: 40, height: 40, cursor: "pointer" }}
                    onClick={() => fileInputRefs.current[t.id]?.click()}
                  >
                    {t.photo_url ? (
                      <img src={absoluteUrl(t.photo_url) || undefined} alt={t.name} />
                    ) : (
                      <span className="photo-placeholder" style={{ fontSize: 14 }}>
                        {t.name.charAt(0)}
                      </span>
                    )}
                  </div>
                  <input
                    ref={(el) => {
                      fileInputRefs.current[t.id] = el;
                    }}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    style={{ display: "none" }}
                    onChange={(e) => handlePhotoChange(t.id, e)}
                  />
                </td>
                <td>{t.name}</td>
                <td>{t.phone}</td>
                <td style={{ textTransform: "capitalize" }}>{t.role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
