import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AssignQuiz from "./AssignQuiz";
import { api } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getSchool: vi.fn(),
      getCurriculumChapters: vi.fn(),
      assignQuiz: vi.fn(),
    },
  };
});

describe("AssignQuiz", () => {
  it("fetches chapters for the selected class/board and lets one or more chapters be picked instead of a topic", async () => {
    vi.mocked(api.getSchool).mockResolvedValue({
      id: 1, name: "Test School", board: null, credit_balance: 0,
    } as never);
    vi.mocked(api.getCurriculumChapters).mockResolvedValue([
      { id: 42, name: "Motion in a Straight Line", chapter_no: 3, subject: "Physics" },
      { id: 43, name: "Work and Energy", chapter_no: 4, subject: "Physics" },
    ]);
    vi.mocked(api.assignQuiz).mockResolvedValue({ assigned_count: 1, assigned: ["Asha"], skipped_already_in_quiz: [] });

    const user = userEvent.setup();
    render(<AssignQuiz />);

    await waitFor(() => expect(screen.getByRole("combobox", { name: /class/i })).toBeInTheDocument());
    await user.selectOptions(screen.getByRole("combobox", { name: /class/i }), "11");

    await waitFor(() => expect(api.getCurriculumChapters).toHaveBeenCalledWith("11", "CBSE"));

    const subjectSelect = await screen.findByRole("combobox", { name: /subject/i });
    await user.selectOptions(subjectSelect, "Physics");

    const chapterCheckbox = await screen.findByText(/Motion in a Straight Line/);
    await user.click(chapterCheckbox);

    // Topic remains enabled and optional once a chapter is picked — it's
    // just a subtopic focus within the selected chapter(s), not replaced.
    const topicInput = screen.getByPlaceholderText("e.g. HCF and LCM only");
    expect(topicInput).not.toBeDisabled();
    expect(topicInput).not.toBeRequired();

    await user.click(screen.getByRole("button", { name: "Assign Quiz" }));

    await waitFor(() =>
      expect(api.assignQuiz).toHaveBeenCalledWith(
        expect.objectContaining({ chapter_ids: [42], topic: undefined, class_: "11" }),
      ),
    );
    expect(await screen.findByText(/Sent to 1 student: Asha/)).toBeInTheDocument();
  });

  it("sends a free-text topic when no chapter is selected", async () => {
    vi.mocked(api.getSchool).mockResolvedValue({
      id: 1, name: "Test School", board: null, credit_balance: 0,
    } as never);
    vi.mocked(api.getCurriculumChapters).mockResolvedValue([]);
    vi.mocked(api.assignQuiz).mockResolvedValue({ assigned_count: 1, assigned: ["Asha"], skipped_already_in_quiz: [] });

    const user = userEvent.setup();
    render(<AssignQuiz />);

    await user.selectOptions(await screen.findByRole("combobox", { name: /class/i }), "8");
    await user.type(screen.getByPlaceholderText("e.g. circular motion"), "photosynthesis");
    await user.click(screen.getByRole("button", { name: "Assign Quiz" }));

    await waitFor(() =>
      expect(api.assignQuiz).toHaveBeenCalledWith(
        expect.objectContaining({ topic: "photosynthesis", chapter_ids: undefined }),
      ),
    );
  });
});
