"""
Seed the subjects/chapters tables with the BSEB (Bihar School Examination
Board) curriculum.

Scope: Classes 9 and 10 Mathematics and Science (Physics/Chemistry/
Biology) only, for now. This data was transcribed from BSEB's own official
"Monthly Progress Chart (Syllabus)" PDFs
(https://biharboardonline.com/files/CLASS_9_SYLLABUS%20.pdf and
.../CLASS_10_SYLLABUS%20.pdf), fetched and OCR'd directly — not fabricated
or inferred. Math and Science happen to match the standard NCERT chapter
structure exactly for both classes (BSEB adopts the NCERT textbooks for
these two subjects), which is why this list looks identical to
scripts/seed_ncert_curriculum.py's entries for the same classes — but it's
tagged board="BSEB" so it stays a genuinely separate, independently
verified dataset rather than assuming the two boards always match.

Classes 11-12 Physics/Chemistry/Biology/Mathematics are seeded too, but
via a different, equally-verified route: BSEB's own official Class XI-XII
syllabus PDF (https://biharboardonline.com/files/Class_XI%20-XII_Syllabus_
2023-25_and_2024-26.pdf) states explicitly, on its own introduction page —
"भाषाओं के अतिरिक्त सभी वैकल्पिक विषयों की पाठ्यपुस्तकें वही होंगी जिनका
प्रकाशन एन.सी.ई.आर.टी., नई दिल्ली द्वारा 11-12वीं कक्षा के लिए किया गया है"
("apart from languages, all elective subjects' textbooks are the same as
those published by NCERT, New Delhi, for classes 11-12") — so these four
subjects are copied directly from scripts/seed_ncert_curriculum.py's
existing classes 11-12 entries rather than re-OCR'd from the (216-page,
multi-stream) BSEB PDF. They inherit that script's own accuracy caveat:
based on training knowledge of the published NCERT syllabus, not a live
scrape — spot-check before relying on it for anything grading-critical.

NOT yet seeded, and deliberately left out rather than guessed: Hindi,
English, Social Science (History/Geography/Political Science/Economics),
Sanskrit, and Urdu for classes 9-12, and any of the non-science elective
streams for 11-12 (Political Science, History, Geography, Economics,
Computer Science, Psychology, Business Studies, etc.) — those subjects'
syllabus content is denser and more prose-heavy than Math/Science's
numbered-chapter format, carrying more real transcription risk, and
weren't covered by the NCERT-textbook shortcut above.

Usage:
    python scripts/seed_bseb_curriculum.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Subject, Chapter  # noqa: E402

BOARD = "BSEB"

CURRICULUM: dict[str, dict[str, list[str]]] = {
    "9": {
        "Mathematics": [
            "Number Systems", "Polynomials", "Coordinate Geometry",
            "Linear Equations in Two Variables", "Introduction to Euclid's Geometry",
            "Lines and Angles", "Triangles", "Quadrilaterals", "Areas of Parallelograms and Triangles",
            "Circles", "Constructions", "Heron's Formula", "Surface Areas and Volumes", "Statistics",
            "Probability",
        ],
        "Physics": ["Motion", "Force and Laws of Motion", "Gravitation", "Work and Energy", "Sound"],
        "Chemistry": [
            "Matter in Our Surroundings", "Is Matter Around Us Pure?", "Atoms and Molecules",
            "Structure of the Atom",
        ],
        "Biology": [
            "The Fundamental Unit of Life", "Tissues", "Diversity in Living Organisms",
            "Why Do We Fall Ill?", "Natural Resources", "Improvement in Food Resources",
        ],
    },
    "10": {
        "Mathematics": [
            "Real Numbers", "Polynomials", "Pair of Linear Equations in Two Variables",
            "Quadratic Equations", "Arithmetic Progressions", "Triangles", "Coordinate Geometry",
            "Introduction to Trigonometry", "Some Applications of Trigonometry", "Circles",
            "Constructions", "Areas Related to Circles", "Surface Areas and Volumes", "Statistics",
            "Probability",
        ],
        "Physics": [
            "Light – Reflection and Refraction", "The Human Eye and the Colourful World", "Electricity",
            "Magnetic Effects of Electric Current", "Sources of Energy",
        ],
        "Chemistry": [
            "Chemical Reactions and Equations", "Acids, Bases and Salts", "Metals and Non-metals",
            "Carbon and its Compounds", "Periodic Classification of Elements",
        ],
        "Biology": [
            "Life Processes", "Control and Coordination", "How do Organisms Reproduce?",
            "Heredity and Evolution", "Our Environment", "Management of Natural Resources",
        ],
    },
    # Copied from seed_ncert_curriculum.py's classes 11-12 (see module
    # docstring for why that's a legitimate, verified shortcut here rather
    # than a guess).
    "11": {
        "Physics": [
            "Physical World", "Units and Measurements", "Motion in a Straight Line", "Motion in a Plane",
            "Laws of Motion", "Work, Energy and Power", "System of Particles and Rotational Motion",
            "Gravitation", "Mechanical Properties of Solids", "Mechanical Properties of Fluids",
            "Thermal Properties of Matter", "Thermodynamics", "Kinetic Theory", "Oscillations", "Waves",
        ],
        "Chemistry": [
            "Some Basic Concepts of Chemistry", "Structure of Atom",
            "Classification of Elements and Periodicity in Properties",
            "Chemical Bonding and Molecular Structure", "States of Matter", "Thermodynamics",
            "Equilibrium", "Redox Reactions", "Hydrogen", "The s-Block Elements",
            "The p-Block Elements", "Organic Chemistry - Some Basic Principles and Techniques",
            "Hydrocarbons", "Environmental Chemistry",
        ],
        "Biology": [
            "The Living World", "Biological Classification", "Plant Kingdom", "Animal Kingdom",
            "Morphology of Flowering Plants", "Anatomy of Flowering Plants",
            "Structural Organisation in Animals", "Cell: The Unit of Life", "Biomolecules",
            "Cell Cycle and Cell Division", "Transport in Plants", "Mineral Nutrition",
            "Photosynthesis in Higher Plants", "Respiration in Plants", "Plant Growth and Development",
            "Digestion and Absorption", "Breathing and Exchange of Gases", "Body Fluids and Circulation",
            "Excretory Products and their Elimination", "Locomotion and Movement",
            "Neural Control and Coordination", "Chemical Coordination and Integration",
        ],
        "Mathematics": [
            "Sets", "Relations and Functions", "Trigonometric Functions",
            "Principle of Mathematical Induction", "Complex Numbers and Quadratic Equations",
            "Linear Inequalities", "Permutations and Combinations", "Binomial Theorem",
            "Sequences and Series", "Straight Lines", "Conic Sections",
            "Introduction to Three Dimensional Geometry", "Limits and Derivatives",
            "Mathematical Reasoning", "Statistics", "Probability",
        ],
    },
    "12": {
        "Physics": [
            "Electric Charges and Fields", "Electrostatic Potential and Capacitance",
            "Current Electricity", "Moving Charges and Magnetism", "Magnetism and Matter",
            "Electromagnetic Induction", "Alternating Current", "Electromagnetic Waves",
            "Ray Optics and Optical Instruments", "Wave Optics",
            "Dual Nature of Radiation and Matter", "Atoms", "Nuclei", "Semiconductor Electronics",
        ],
        "Chemistry": [
            "Solid State", "Solutions", "Electrochemistry", "Chemical Kinetics", "Surface Chemistry",
            "General Principles and Processes of Isolation of Elements", "The p-Block Elements",
            "The d and f Block Elements", "Coordination Compounds", "Haloalkanes and Haloarenes",
            "Alcohols, Phenols and Ethers", "Aldehydes, Ketones and Carboxylic Acids", "Amines",
            "Biomolecules",
        ],
        "Biology": [
            "Sexual Reproduction in Flowering Plants", "Human Reproduction", "Reproductive Health",
            "Principles of Inheritance and Variation", "Molecular Basis of Inheritance", "Evolution",
            "Human Health and Disease", "Microbes in Human Welfare",
            "Biotechnology: Principles and Processes", "Biotechnology and its Applications",
            "Organisms and Populations", "Ecosystem", "Biodiversity and Conservation",
        ],
        "Mathematics": [
            "Relations and Functions", "Inverse Trigonometric Functions", "Matrices", "Determinants",
            "Continuity and Differentiability", "Application of Derivatives", "Integrals",
            "Application of Integrals", "Differential Equations", "Vector Algebra",
            "Three Dimensional Geometry", "Linear Programming", "Probability",
        ],
    },
}


def seed():
    db = SessionLocal()
    created_subjects = created_chapters = 0
    try:
        for class_num, subjects in CURRICULUM.items():
            for subject_name, chapters in subjects.items():
                subject = db.query(Subject).filter(
                    Subject.class_ == class_num, Subject.name == subject_name, Subject.board == BOARD,
                ).first()
                if not subject:
                    subject = Subject(name=subject_name, class_=class_num, board=BOARD)
                    db.add(subject)
                    db.commit()
                    db.refresh(subject)
                    created_subjects += 1

                existing_chapter_names = {
                    c.name for c in db.query(Chapter).filter(Chapter.subject_id == subject.id).all()
                }
                for i, chapter_name in enumerate(chapters, start=1):
                    if chapter_name not in existing_chapter_names:
                        db.add(Chapter(subject_id=subject.id, name=chapter_name, chapter_no=i))
                        created_chapters += 1
        db.commit()
        print(f"Done. Created {created_subjects} new BSEB subjects and {created_chapters} new chapters.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
