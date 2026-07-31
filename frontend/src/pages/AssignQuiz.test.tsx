import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AssignQuiz from "./AssignQuiz";
import { api, type Student } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listStudents: vi.fn(),
      getCurriculumChapters: vi.fn(),
      assignQuiz: vi.fn(),
    },
  };
});

const STUDENTS: Student[] = [
  {
    id: 1, name: "Asha", phone: "919000000001", class: "11", board: "CBSE", school: null, gender: null,
    focus_topic: null, features: {}, hints_given_count: 0, direct_solutions_count: 0, credit_balance: 0,
    centre_id: 1, referral_code: null, referral_credits_earned: 0, photo_url: null, parent_phone: null,
    parent_name: null, subscription_plan: "credits", subscription_expires_at: null,
  },
];

describe("AssignQuiz", () => {
  it("fetches chapters for the selected class and lets the topic field be replaced by a chapter pick", async () => {
    vi.mocked(api.listStudents).mockResolvedValue(STUDENTS);
    vi.mocked(api.getCurriculumChapters).mockResolvedValue([
      { id: 42, name: "Motion in a Straight Line", chapter_no: 3, subject: "Physics" },
    ]);
    vi.mocked(api.assignQuiz).mockResolvedValue({ assigned_count: 1, assigned: ["Asha"], skipped_already_in_quiz: [] });

    const user = userEvent.setup();
    render(<AssignQuiz />);

    await waitFor(() => expect(screen.getByRole("combobox", { name: /class/i })).toBeInTheDocument());
    await user.selectOptions(screen.getByRole("combobox", { name: /class/i }), "11");

    await waitFor(() => expect(api.getCurriculumChapters).toHaveBeenCalledWith("11"));
    const chapterSelect = await screen.findByRole("combobox", { name: /chapter/i });
    await user.selectOptions(chapterSelect, "42");

    // Topic becomes disabled once a chapter is picked — the chapter wins.
    expect(screen.getByPlaceholderText("e.g. circular motion")).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Assign Quiz" }));

    await waitFor(() =>
      expect(api.assignQuiz).toHaveBeenCalledWith(
        expect.objectContaining({ chapter_id: 42, topic: undefined, class_: "11" }),
      ),
    );
    expect(await screen.findByText(/Sent to 1 student: Asha/)).toBeInTheDocument();
  });

  it("sends a free-text topic when no chapter is selected", async () => {
    vi.mocked(api.listStudents).mockResolvedValue(STUDENTS);
    vi.mocked(api.getCurriculumChapters).mockResolvedValue([]);
    vi.mocked(api.assignQuiz).mockResolvedValue({ assigned_count: 1, assigned: ["Asha"], skipped_already_in_quiz: [] });

    const user = userEvent.setup();
    render(<AssignQuiz />);

    await user.type(screen.getByPlaceholderText("e.g. circular motion"), "photosynthesis");
    await user.click(screen.getByRole("button", { name: "Assign Quiz" }));

    await waitFor(() =>
      expect(api.assignQuiz).toHaveBeenCalledWith(
        expect.objectContaining({ topic: "photosynthesis", chapter_id: undefined }),
      ),
    );
  });
});
