import { Navigate, Route, BrowserRouter, Routes } from "react-router-dom";
import { isParentAuthenticated, isStudentAuthenticated } from "./api";
import Login from "./pages/Login";
import Register from "./pages/Register";
import ForgotPassword from "./pages/ForgotPassword";
import StudentList from "./pages/StudentList";
import StudentDetail from "./pages/StudentDetail";
import BulkUpload from "./pages/BulkUpload";
import Credits from "./pages/Credits";
import Teachers from "./pages/Teachers";
import SchoolProfile from "./pages/SchoolProfile";
import Analytics from "./pages/Analytics";
import Schools from "./pages/Schools";
import Workbook from "./pages/Workbook";
import Presentations from "./pages/Presentations";
import MyTutor from "./pages/MyTutor";
import Chat from "./pages/Chat";
import ParentDashboard from "./pages/ParentDashboard";
import Pay from "./pages/Pay";
import Layout from "./components/Layout";
import StudentLayout from "./components/StudentLayout";

function isAuthenticated() {
  return !!localStorage.getItem("token");
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  if (!isAuthenticated()) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function StudentProtectedRoute({ children }: { children: React.ReactNode }) {
  if (!isStudentAuthenticated()) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function ParentProtectedRoute({ children }: { children: React.ReactNode }) {
  if (!isParentAuthenticated()) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/pay" element={<Pay />} />
        <Route
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route path="/students" element={<StudentList />} />
          <Route path="/students/:id" element={<StudentDetail />} />
          <Route path="/bulk-upload" element={<BulkUpload />} />
          <Route path="/credits" element={<Credits />} />
          <Route path="/teachers" element={<Teachers />} />
          <Route path="/school-profile" element={<SchoolProfile />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/schools" element={<Schools />} />
          <Route path="/workbook" element={<Workbook />} />
          <Route path="/presentations" element={<Presentations />} />
          <Route path="/my-tutor" element={<MyTutor />} />
          <Route path="/" element={<Navigate to="/students" replace />} />
        </Route>
        <Route
          element={
            <StudentProtectedRoute>
              <StudentLayout />
            </StudentProtectedRoute>
          }
        >
          <Route path="/chat" element={<Chat />} />
        </Route>
        <Route
          path="/parent"
          element={
            <ParentProtectedRoute>
              <ParentDashboard />
            </ParentProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
