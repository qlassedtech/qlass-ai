// Set VITE_API_BASE in a .env(.local) file to point at a deployed backend —
// see .env.example. Falls back to localhost for local dev with no .env.
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

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

async function requestStudentMultipart(path: string, formData: FormData) {
  const token = getStudentToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  // No Content-Type set here on purpose — same reasoning as requestMultipart.
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers, body: formData });
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
  photo_url: string | null;
  centre_id: number | null;
  organization_id: number | null;
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
  subscription_plan: string;
  subscription_expires_at: string | null;
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
  board: string | null;
  logo_url: string | null;
  credit_balance: number;
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
  upsell_candidates: { id: number; name: string; phone: string; spend_this_month: number }[];
  at_risk_students: {
    id: number;
    name: string;
    phone: string;
    accuracy_pct: number | null;
    consecutive_unresolved_hints: number;
  }[];
}

export interface DeletionRequest {
  id: number;
  name: string;
  phone: string;
  requested_at: string;
}

export interface SchoolOverview {
  id: number;
  name: string;
  city: string | null;
  sales_status: string;
  sales_notes: string | null;
  contract_notes: string | null;
  student_count: number;
  school_credit_balance: number;
  last_activity: string | null;
  days_inactive: number | null;
  is_churn_risk: boolean;
  pilot_status: string;
  pilot_started_at: string | null;
  pilot_expires_at: string | null;
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
  previewStudentBulkUpload: (file: File, centreId?: number) => {
    const formData = new FormData();
    formData.append("file", file);
    const query = centreId ? `?centre_id=${centreId}` : "";
    return requestMultipart(`/admin/students/bulk-upload/preview${query}`, formData) as Promise<{
      rows: Record<string, string | null>[];
    }>;
  },
  confirmStudentBulkUpload: (
    rows: Record<string, string | null>[], features: Record<string, boolean>, centreId?: number,
  ) =>
    request("/admin/students/bulk-upload/confirm", {
      method: "POST",
      body: JSON.stringify({ rows, features, centre_id: centreId }),
    }) as Promise<{ created: string[]; updated: string[]; skipped: unknown[] }>,
  previewTeacherBulkUpload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestMultipart("/admin/teachers/bulk-upload/preview", formData) as Promise<{
      rows: Record<string, string | null>[];
    }>;
  },
  confirmTeacherBulkUpload: (rows: Record<string, string | null>[]) =>
    request("/admin/teachers/bulk-upload/confirm", {
      method: "POST",
      body: JSON.stringify({ rows }),
    }) as Promise<{
      created_count: number;
      created: string[];
      skipped_count: number;
      generated_passwords: Record<string, string>;
    }>,
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
  addStudentCredits: (id: number, amount: number, note?: string, reason: "refund" | "goodwill" | "correction" = "goodwill") =>
    request(`/admin/students/${id}/credits/add`, {
      method: "POST",
      body: JSON.stringify({ amount, note, reason }),
    }) as Promise<{ balance: number }>,
  setStudentSubscription: (
    id: number,
    data: {
      plan: "credits" | "unlimited";
      duration_days?: number;
      is_trial?: boolean;
      payment_reference?: string;
      note?: string;
    },
  ) =>
    request(`/admin/students/${id}/subscription`, {
      method: "POST",
      body: JSON.stringify(data),
    }) as Promise<{ subscription_plan: string; subscription_expires_at: string | null }>,
  activateTeacherTutorSubscription: (
    teacherId: number,
    data: { duration_days?: number; is_trial?: boolean; payment_reference?: string; note?: string } = {},
  ) =>
    request(`/admin/teachers/${teacherId}/my-tutor-subscription/activate`, {
      method: "POST",
      body: JSON.stringify(data),
    }) as Promise<{
      subscription_plan: string;
      subscription_expires_at: string | null;
    }>,
  activateSchoolTrialSubscriptions: (
    centreId: number,
    data: { duration_days: number; student_ids?: number[]; teacher_ids?: number[]; note?: string },
  ) =>
    request(`/admin/schools/${centreId}/trial-subscriptions`, {
      method: "POST",
      body: JSON.stringify(data),
    }) as Promise<{ activated_count: number; activated: string[] }>,
  launchSchoolPilot: (
    centreId: number,
    data: { duration_days: number; credits_per_student: number; teacher_tool_credits?: number; student_ids: number[]; note?: string },
  ) =>
    request(`/admin/schools/${centreId}/pilot`, {
      method: "POST",
      body: JSON.stringify(data),
    }) as Promise<{ pilot_status: string; pilot_expires_at: string; credits_per_student: number; teacher_tool_credits: number; enabled_features: string[]; granted_count: number; granted: string[] }>,
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
  getAnalytics: (centreId?: number) =>
    request(`/admin/analytics${centreId ? `?centre_id=${centreId}` : ""}`) as Promise<Analytics>,
  downloadSchoolStatement: (year: number, month: number, centreId?: number) =>
    requestBlob(`/admin/school/statement?year=${year}&month=${month}${centreId ? `&centre_id=${centreId}` : ""}`),
  getDeletionRequests: () => request("/admin/deletion-requests") as Promise<DeletionRequest[]>,
  fulfillDeletion: (id: number) =>
    request(`/admin/students/${id}/fulfill-deletion`, { method: "POST" }) as Promise<{ deleted: boolean }>,
  getSchoolsOverview: () => request("/admin/schools") as Promise<SchoolOverview[]>,
  updateSchoolSales: (
    id: number,
    data: { sales_status?: string; sales_notes?: string; contract_notes?: string },
  ) => request(`/admin/schools/${id}/sales`, { method: "PATCH", body: JSON.stringify(data) }) as Promise<SchoolOverview>,
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
  generateWorkbook: (data: {
    topic?: string;
    chapter_ids?: number[];
    class_?: string;
    board?: string;
    num_questions: number;
    include_answer_key: boolean;
  }) => requestBlob("/admin/workbook/generate", { method: "POST", body: JSON.stringify(data) }),
  assignQuiz: (data: {
    topic?: string;
    chapter_ids?: number[];
    class_?: string;
    board?: string;
    phone_numbers?: string[];
  }) =>
    request("/admin/quizzes/assign", { method: "POST", body: JSON.stringify(data) }) as Promise<{
      assigned_count: number;
      assigned: string[];
      skipped_already_in_quiz: string[];
    }>,
  getCurriculumChapters: (classNum: string, board: string = "CBSE") =>
    request(
      `/admin/curriculum/chapters?class_=${encodeURIComponent(classNum)}&board=${encodeURIComponent(board)}`,
    ) as Promise<{ id: number; name: string; chapter_no: number | null; subject: string }[]>,
  generatePresentation: (data: {
    topic?: string;
    chapter_ids?: number[];
    class_?: string;
    board?: string;
    num_cards: number;
  }) =>
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
  getMyTutor: () =>
    request("/admin/my-tutor") as Promise<{
      id: number;
      credit_balance: number;
      referral_code: string;
      subscription_plan: string;
      subscription_expires_at: string | null;
      auto_renewing: boolean;
    }>,
  createMyTutorSubscription: () =>
    request("/admin/my-tutor/subscription/create", { method: "POST" }) as Promise<CreateSubscriptionResponse>,
  verifyMyTutorSubscription: (data: {
    razorpay_subscription_id: string;
    razorpay_payment_id: string;
    razorpay_signature: string;
  }) =>
    request("/admin/my-tutor/subscription/verify", { method: "POST", body: JSON.stringify(data) }) as Promise<{
      subscription_plan: string;
      subscription_expires_at: string | null;
    }>,
  cancelMyTutorSubscription: () =>
    request("/admin/my-tutor/subscription/cancel", { method: "POST" }) as Promise<{
      cancelled: boolean;
      access_until: string | null;
    }>,
  getMyTutorHistory: () => request("/admin/my-tutor/history") as Promise<ChatMessage[]>,
  sendMyTutorMessage: (message: string) =>
    request("/admin/my-tutor/chat/send", { method: "POST", body: JSON.stringify({ message }) }) as Promise<{
      reply: string;
      credit_balance: number;
    }>,
  sendMyTutorImage: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestMultipart("/admin/my-tutor/chat/send-image", formData) as Promise<ChatReplyResponse>;
  },
  sendMyTutorVoice: (file: Blob, filename: string) => {
    const formData = new FormData();
    formData.append("file", file, filename);
    return requestMultipart("/admin/my-tutor/chat/send-voice", formData) as Promise<ChatReplyResponse>;
  },
  sendMyTutorDocument: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestMultipart("/admin/my-tutor/chat/send-document", formData) as Promise<ChatReplyResponse>;
  },
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
export interface ChatReplyResponse {
  reply: string;
  credit_balance: number;
}

export const studentApi = {
  me: () => requestStudent("/student-app/me") as Promise<StudentProfile>,
  history: () => requestStudent("/student-app/chat/history") as Promise<ChatMessage[]>,
  sendMessage: (message: string) =>
    requestStudent("/student-app/chat/send", { method: "POST", body: JSON.stringify({ message }) }) as Promise<ChatReplyResponse>,
  // Same OCR/STT/document pipeline WhatsApp uses (see backend
  // app.routers.student_app) — the extracted text becomes an ordinary chat
  // turn, so the response shape matches sendMessage exactly.
  sendImage: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestStudentMultipart("/student-app/chat/send-image", formData) as Promise<ChatReplyResponse>;
  },
  sendVoice: (file: Blob, filename: string) => {
    const formData = new FormData();
    formData.append("file", file, filename);
    return requestStudentMultipart("/student-app/chat/send-voice", formData) as Promise<ChatReplyResponse>;
  },
  sendDocument: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return requestStudentMultipart("/student-app/chat/send-document", formData) as Promise<ChatReplyResponse>;
  },
};

export interface CreateOrderResponse {
  order_id: string;
  key_id: string;
  amount: number;
  currency: string;
}

export interface CreateSubscriptionResponse {
  subscription_id: string;
  key_id: string;
}

// Public — no teacher/admin login involved, parents/students pay directly.
export const payApi = {
  // student_id disambiguates a shared family phone with more than one
  // child on it — every caller of this API already knows which specific
  // student a payment is for, so it's passed through rather than left for
  // the backend to guess (which previously always picked whichever
  // sibling had the lowest database id, regardless of who a link/button
  // was actually generated for).
  createOrder: (phone: string, amount: number, studentId?: number) =>
    request("/pay/create-order", {
      method: "POST", body: JSON.stringify({ phone, amount, student_id: studentId }),
    }) as Promise<CreateOrderResponse>,
  verify: (data: {
    razorpay_order_id: string;
    razorpay_payment_id: string;
    razorpay_signature: string;
    phone: string;
    student_id?: number;
  }) => request("/pay/verify", { method: "POST", body: JSON.stringify(data) }) as Promise<{ credited: number; balance: number }>,
  createSubscription: (phone: string, studentId?: number) =>
    request("/pay/create-subscription", {
      method: "POST",
      body: JSON.stringify({ phone, student_id: studentId }),
    }) as Promise<CreateSubscriptionResponse>,
  verifySubscription: (data: {
    razorpay_subscription_id: string;
    razorpay_payment_id: string;
    razorpay_signature: string;
    phone: string;
    student_id?: number;
  }) =>
    request("/pay/verify-subscription", { method: "POST", body: JSON.stringify(data) }) as Promise<{
      subscription_plan: string;
      subscription_expires_at: string | null;
    }>,
};

export interface ParentProfile {
  parent_name: string;
  student_id: number;
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
  getConsent: () =>
    requestParent("/parent-app/consent") as Promise<{ statement: string; given: boolean; given_at: string | null }>,
  giveConsent: () =>
    requestParent("/parent-app/consent", { method: "POST" }) as Promise<{ given: boolean; given_at: string }>,
  requestDeletion: () =>
    requestParent("/parent-app/request-deletion", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    }) as Promise<{ requested: boolean; requested_at: string }>,
};

// No auth token attached on purpose — the self-registration landing page
// (see pages/Join.tsx) is reachable by anyone with the link, logged in or
// not, so it deliberately doesn't go through request()'s token-attaching
// logic (harmless either way since the backend endpoints don't check auth,
// but this keeps intent obvious).
export const publicApi = {
  schoolInfo: (school: string) =>
    fetch(`${API_BASE}/public/school-info?school=${encodeURIComponent(school)}`).then((res) => res.json()) as Promise<{
      name: string | null;
      logo_url: string | null;
    }>,
  register: (data: { name: string; phone: string; school?: string }) =>
    fetch(`${API_BASE}/public/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    }).then((res) => res.json()) as Promise<{ success: boolean; already_registered?: boolean; error?: string }>,
};
