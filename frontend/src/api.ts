export const API_BASE = "http://localhost:8000";

function getToken(): string | null {
  return localStorage.getItem("token");
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem("token", token);
  else localStorage.removeItem("token");
}

// A separate key/session from the teacher token above — the unified login
// page can issue either kind depending on the phone number entered (see
// api.checkPhone), and both can coexist since they're stored separately.
function getStudentToken(): string | null {
  return localStorage.getItem("student_token");
}

export function setStudentToken(token: string | null) {
  if (token) localStorage.setItem("student_token", token);
  else localStorage.removeItem("student_token");
}

export function isStudentAuthenticated(): boolean {
  return !!getStudentToken();
}

// A third, independent session — a parent's read-only view of their
// child's progress/billing, distinct from both the teacher and student
// sessions above.
function getParentToken(): string | null {
  return localStorage.getItem("parent_token");
}

export function setParentToken(token: string | null) {
  if (token) localStorage.setItem("parent_token", token);
  else localStorage.removeItem("parent_token");
}

export function isParentAuthenticated(): boolean {
  return !!getParentToken();
}

async function request(path: string, options: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    setToken(null);
    window.location.href = "/login";
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

async function requestMultipart(path: string, formData: FormData) {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  // No Content-Type set here on purpose — the browser sets the correct
  // multipart/form-data boundary itself; setting it manually breaks the upload.
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers, body: formData });
  if (res.status === 401) {
    setToken(null);
    window.location.href = "/login";
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

async function requestBlob(path: string, options: RequestInit = {}) {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    setToken(null);
    window.location.href = "/login";
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.blob();
}

async function requestStudent(path: string, options: RequestInit = {}) {
  const token = getStudentToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    setStudentToken(null);
    window.location.href = "/login";
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

async function requestParent(path: string, options: RequestInit = {}) {
  const token = getParentToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    setParentToken(null);
    window.location.href = "/login";
    throw new Error("Not authenticated");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export interface Teacher {
  id: number;
  name: string;
  role: string;
  phone: string;
}

export interface Student {
  id: number;
  name: string;
  phone: string;
  class: string | null;
  board: string | null;
  school: string | null;
  gender: string | null;
  focus_topic: string | null;
  features: Record<string, boolean>;
  hints_given_count: number;
  direct_solutions_count: number;
  credit_balance: number;
  centre_id: number | null;
  referral_code: string | null;
  referral_credits_earned: number;
  photo_url: string | null;
  parent_phone: string | null;
  parent_name: string | null;
}

export interface TeacherAccount {
  id: number;
  name: string;
  phone: string;
  role: string;
  centre_id: number | null;
  photo_url: string | null;
}

export interface School {
  id: number;
  name: string;
  city: string | null;
  logo_url: string | null;
}

export interface Analytics {
  total_students: number;
  active_this_week: number;
  inactive_students: { id: number; name: string; phone: string; days_since_last_message: number | null }[];
  avg_accuracy_pct: number | null;
  top_weak_topics: { topic: string; incorrect_count: number }[];
  total_credit_spend_this_month: number;
  workbook_generations_this_month: number;
  presentation_generations_this_month: number;
}

export function absoluteUrl(path: string | null): string | null {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_BASE}${path}`;
}

export interface ProgressResponse {
  stats: {
    total_evaluated: number;
    correct: number;
    incorrect: number;
    accuracy_pct: number | null;
    weak_topics: string[];
    messages_sent: number;
  };
  activity: {
    active_days: number;
    streak_days: number;
    days_since_last_message: number | null;
  };
  coverage: { covered: string[]; not_covered: string[]; total: number } | null;
}

export const api = {
  login: (phone: string, password: string) =>
    request("/auth/login", { method: "POST", body: JSON.stringify({ phone, password }) }) as Promise<{
      access_token: string;
      teacher: Teacher;
    }>,
  me: () => request("/admin/me") as Promise<Teacher>,
  listStudents: () => request("/admin/students") as Promise<Student[]>,
  createStudent: (data: { name: string; phone: string; class_?: string; board?: string; school?: string }) =>
    request("/admin/students", { method: "POST", body: JSON.stringify(data) }) as Promise<Student>,
  bulkUploadStudents: (file: File, features: Record<string, boolean>) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("features", JSON.stringify(features));
    return requestMultipart("/admin/students/bulk-upload", formData) as Promise<{
      created: string[];
      updated: string[];
      skipped: unknown[];
    }>;
  },
  updateStudent: (id: number, data: Partial<Student> & { class_?: string }) =>
    request(`/admin/students/${id}`, { method: "PATCH", body: JSON.stringify(data) }) as Promise<Student>,
  getProgress: (id: number) => request(`/admin/students/${id}/progress`) as Promise<ProgressResponse>,
  sendDigest: (id: number, toPhone: string) =>
    request(`/admin/students/${id}/digest`, { method: "POST", body: JSON.stringify({ to_phone: toPhone }) }),
  sendPaymentLink: (id: number, toPhone?: string) =>
    request(`/admin/students/${id}/send-payment-link`, {
      method: "POST",
      body: JSON.stringify({ to_phone: toPhone }),
    }) as Promise<{ sent: boolean }>,
  getStudentCredits: (id: number) => request(`/admin/students/${id}/credits`) as Promise<{ balance: number }>,
  addStudentCredits: (id: number, amount: number, note?: string) =>
    request(`/admin/students/${id}/credits/add`, {
      method: "POST",
      body: JSON.stringify({ amount, note }),
    }) as Promise<{ balance: number }>,
  registerSchool: (data: { school_name: string; city?: string; admin_name: string; admin_phone: string; password: string }) =>
    request("/auth/register-school", { method: "POST", body: JSON.stringify(data) }) as Promise<{
      access_token: string;
      teacher: Teacher;
    }>,
  forgotPassword: (phone: string) =>
    request("/auth/forgot-password", { method: "POST", body: JSON.stringify({ phone }) }) as Promise<{ sent: boolean }>,
  resetPassword: (phone: string, otp: string, new_password: string) =>
    request("/auth/reset-password", { method: "POST", body: JSON.stringify({ phone, otp, new_password }) }) as Promise<{
      reset: boolean;
    }>,
  listTeachers: () => request("/admin/teachers") as Promise<TeacherAccount[]>,
  createTeacher: (data: { name: string; phone: string; password: string; role: string; centre_id?: number }) =>
    request("/admin/teachers", { method: "POST", body: JSON.stringify(data) }) as Promise<TeacherAccount>,
  getSchool: () => request("/admin/school") as Promise<School>,
  getAnalytics: () => request("/admin/analytics") as Promise<Analytics>,
  uploadSchoolLogo: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestMultipart("/admin/school/logo", formData) as Promise<{ logo_url: string }>;
  },
  uploadStudentPhoto: (id: number, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestMultipart(`/admin/students/${id}/photo`, formData) as Promise<{ photo_url: string }>;
  },
  uploadTeacherPhoto: (id: number, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestMultipart(`/admin/teachers/${id}/photo`, formData) as Promise<{ photo_url: string }>;
  },
  generateWorkbook: (data: { topic: string; class_?: string; num_questions: number; include_answer_key: boolean }) =>
    requestBlob("/admin/workbook/generate", { method: "POST", body: JSON.stringify(data) }),
  generatePresentation: (data: { topic: string; num_cards: number }) =>
    request("/admin/presentation/generate", { method: "POST", body: JSON.stringify(data) }) as Promise<{
      generation_id: string;
    }>,
  getPresentationStatus: (generationId: string) =>
    request(`/admin/presentation/status/${generationId}`) as Promise<{ status: string; url: string | null }>,
  checkPhone: (phone: string) =>
    request("/student-app/auth/check-phone", { method: "POST", body: JSON.stringify({ phone }) }) as Promise<{
      login_type: "password" | "otp" | "parent_otp";
    }>,
  requestStudentOtp: (phone: string) =>
    request("/student-app/auth/request-otp", { method: "POST", body: JSON.stringify({ phone }) }) as Promise<{
      sent: boolean;
    }>,
  verifyStudentOtp: (phone: string, otp: string, name?: string, referral_code?: string) =>
    request("/student-app/auth/verify-otp", {
      method: "POST", body: JSON.stringify({ phone, otp, name, referral_code }),
    }) as Promise<{ access_token: string; student: StudentProfile }>,
  getMyTutor: () => request("/admin/my-tutor") as Promise<{ id: number; credit_balance: number; referral_code: string }>,
  getMyTutorHistory: () => request("/admin/my-tutor/history") as Promise<ChatMessage[]>,
  sendMyTutorMessage: (message: string) =>
    request("/admin/my-tutor/chat/send", { method: "POST", body: JSON.stringify({ message }) }) as Promise<{
      reply: string;
      credit_balance: number;
    }>,
};

export interface StudentProfile {
  id: number;
  name: string;
  class: string | null;
  board: string | null;
  focus_topic: string | null;
  credit_balance: number;
  referral_code: string | null;
}

export interface ChatMessage {
  role: "user" | "assistant";
  message: string;
  created_at: string;
}

// Uses the separate student session (see setStudentToken) — the student
// chat app's own auth, distinct from the teacher/admin portal above.
export const studentApi = {
  me: () => requestStudent("/student-app/me") as Promise<StudentProfile>,
  history: () => requestStudent("/student-app/chat/history") as Promise<ChatMessage[]>,
  sendMessage: (message: string) =>
    requestStudent("/student-app/chat/send", { method: "POST", body: JSON.stringify({ message }) }) as Promise<{
      reply: string;
      credit_balance: number;
    }>,
};

export interface CreateOrderResponse {
  order_id: string;
  key_id: string;
  amount: number;
  currency: string;
}

// Public — no teacher/admin login involved, parents/students pay directly.
export const payApi = {
  createOrder: (phone: string, amount: number) =>
    request("/pay/create-order", { method: "POST", body: JSON.stringify({ phone, amount }) }) as Promise<CreateOrderResponse>,
  verify: (data: {
    razorpay_order_id: string;
    razorpay_payment_id: string;
    razorpay_signature: string;
    phone: string;
  }) => request("/pay/verify", { method: "POST", body: JSON.stringify(data) }) as Promise<{ credited: number; balance: number }>,
};

export interface ParentProfile {
  parent_name: string;
  student_name: string;
  student_phone: string;
  class: string | null;
  credit_balance: number;
}

export interface ParentProgress {
  stats: {
    total_evaluated: number;
    correct: number;
    incorrect: number;
    accuracy_pct: number | null;
    weak_topics: string[];
    messages_sent: number;
  };
  activity: {
    active_days: number;
    streak_days: number;
    days_since_last_message: number | null;
  };
  coverage: { covered: string[]; not_covered: string[]; total: number } | null;
}

// A parent's own read-only session — distinct from teacher and student
// sessions above.
export const parentApi = {
  requestOtp: (phone: string) =>
    request("/parent-app/auth/request-otp", { method: "POST", body: JSON.stringify({ phone }) }) as Promise<{
      sent: boolean;
    }>,
  verifyOtp: (phone: string, otp: string) =>
    request("/parent-app/auth/verify-otp", { method: "POST", body: JSON.stringify({ phone, otp }) }) as Promise<{
      access_token: string;
      parent: { id: number; name: string | null };
    }>,
  me: () => requestParent("/parent-app/me") as Promise<ParentProfile>,
  progress: () => requestParent("/parent-app/progress") as Promise<ParentProgress>,
};
