"""
Seed the subjects/chapters tables with the standard NCERT curriculum.

Scope: Mathematics, Science (Physics/Chemistry/Biology for 11-12), and
Social Science (History/Geography/Political Science/Economics) for classes
6-12 — the core subjects this tutor actually gets asked about in practice.
Classes 1-5 are seeded at subject level only (Maths, EVS, English, Hindi)
without chapter-level detail, since early-primary NCERT books are far less
chapter-standardized and confidence in exact titles is much lower there.

IMPORTANT: this chapter data comes from training knowledge of the
widely-published NCERT syllabus, not a live/authoritative source scrape.
NCERT periodically revises textbooks (e.g. the 2023 content rationalisation
merged/dropped some chapters), so treat this as a strong starting point and
spot-check chapter names/order against the current official NCERT site
before relying on it for anything graded or syllabus-compliance-critical.

Usage:
    python scripts/seed_ncert_curriculum.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import SessionLocal  # noqa: E402
from app.models.core import Subject, Chapter  # noqa: E402

# class -> subject name -> [chapter names in order]
CURRICULUM: dict[str, dict[str, list[str]]] = {
    "6": {
        "Mathematics": [
            "Knowing Our Numbers", "Whole Numbers", "Playing with Numbers", "Basic Geometrical Ideas",
            "Understanding Elementary Shapes", "Integers", "Fractions", "Decimals", "Data Handling",
            "Mensuration", "Algebra", "Ratio and Proportion", "Symmetry", "Practical Geometry",
        ],
        "Science": [
            "Food: Where Does It Come From?", "Components of Food", "Fibre to Fabric",
            "Sorting Materials into Groups", "Separation of Substances", "Changes Around Us",
            "Getting to Know Plants", "Body Movements", "The Living Organisms and Their Surroundings",
            "Motion and Measurement of Distances", "Light, Shadows and Reflections",
            "Electricity and Circuits", "Fun with Magnets", "Water", "Air Around Us",
            "Garbage In, Garbage Out",
        ],
        "History": [
            "What, Where, How and When?", "From Hunting-Gathering to Growing Food",
            "In the Earliest Cities", "What Books and Burials Tell Us",
            "Kingdoms, Kings and an Early Republic", "New Questions and Ideas",
            "From a Kingdom to an Empire", "Villages, Towns and Trade",
            "New Empires and Kingdoms", "Buildings, Paintings and Books",
        ],
        "Geography": [
            "The Earth in the Solar System", "Globe: Latitudes and Longitudes", "Motions of the Earth",
            "Maps", "Major Domains of the Earth", "Major Landforms of the Earth",
            "Our Country - India", "India: Climate, Vegetation and Wildlife",
        ],
        "Political Science": [
            "Understanding Diversity", "Diversity and Discrimination", "What is Government?",
            "Key Elements of a Democratic Government", "Panchayati Raj", "Rural Administration",
            "Urban Administration", "Rural Livelihoods", "Urban Livelihoods",
        ],
    },
    "7": {
        "Mathematics": [
            "Integers", "Fractions and Decimals", "Data Handling", "Simple Equations",
            "Lines and Angles", "The Triangle and its Properties", "Congruence of Triangles",
            "Comparing Quantities", "Rational Numbers", "Practical Geometry", "Perimeter and Area",
            "Algebraic Expressions", "Exponents and Powers", "Symmetry", "Visualising Solid Shapes",
        ],
        "Science": [
            "Nutrition in Plants", "Nutrition in Animals", "Fibre to Fabric", "Heat",
            "Acids, Bases and Salts", "Physical and Chemical Changes",
            "Weather, Climate and Adaptations of Animals to Climate", "Winds, Storms and Cyclones",
            "Soil", "Respiration in Organisms", "Transportation in Animals and Plants",
            "Reproduction in Plants", "Motion and Time", "Electric Current and its Effects", "Light",
            "Water: A Precious Resource", "Forests: Our Lifeline", "Wastewater Story",
        ],
        "History": [
            "Tracing Changes Through A Thousand Years", "New Kings and Kingdoms", "The Delhi Sultans",
            "The Mughal Empire", "Rulers and Buildings", "Towns, Traders and Craftspersons",
            "Tribes, Nomads and Settled Communities", "Devotional Paths to the Divine",
            "The Making of Regional Cultures", "Eighteenth-Century Political Formations",
        ],
        "Geography": [
            "Environment", "Inside Our Earth", "Our Changing Earth", "Air", "Water",
            "Natural Vegetation and Wildlife", "Human Environment - Settlement, Transport and Communication",
            "Human-Environment Interactions: The Tropical and the Subtropical Region",
        ],
        "Political Science": [
            "On Equality", "Role of the Government in Health", "How the State Government Works",
            "Growing up as Boys and Girls", "Women Change the World", "Understanding Media",
            "Understanding Advertising", "Markets Around Us", "A Shirt in the Market",
            "Struggles for Equality",
        ],
    },
    "8": {
        "Mathematics": [
            "Rational Numbers", "Linear Equations in One Variable", "Understanding Quadrilaterals",
            "Practical Geometry", "Data Handling", "Squares and Square Roots", "Cubes and Cube Roots",
            "Comparing Quantities", "Algebraic Expressions and Identities", "Visualising Solid Shapes",
            "Mensuration", "Exponents and Powers", "Direct and Inverse Proportions", "Factorisation",
            "Introduction to Graphs", "Playing with Numbers",
        ],
        "Science": [
            "Crop Production and Management", "Microorganisms: Friend and Foe",
            "Synthetic Fibres and Plastics", "Materials: Metals and Non-Metals",
            "Coal and Petroleum", "Combustion and Flame", "Conservation of Plants and Animals",
            "Cell - Structure and Functions", "Reproduction in Animals",
            "Reaching the Age of Adolescence", "Force and Pressure", "Friction", "Sound",
            "Chemical Effects of Electric Current", "Some Natural Phenomena", "Light",
            "Stars and the Solar System", "Pollution of Air and Water",
        ],
        "History": [
            "How, When and Where", "From Trade to Territory", "Ruling the Countryside",
            "Tribals, Dikus and the Vision of a Golden Age", "When People Rebel 1857 and After",
            "Weavers, Iron Smelters and Factory Owners",
            "Civilising the 'Native', Educating the Nation", "Women, Caste and Reform",
            "The Making of the National Movement 1870s-1947", "India After Independence",
        ],
        "Geography": [
            "Resources", "Land, Soil, Water, Natural Vegetation and Wildlife Resources",
            "Mineral and Power Resources", "Agriculture", "Industries", "Human Resources",
        ],
        "Political Science": [
            "The Indian Constitution", "Understanding Secularism", "Why do we need a Parliament?",
            "Understanding Laws", "Judiciary", "Understanding Our Criminal Justice System",
            "Understanding Marginalisation", "Confronting Marginalisation", "Public Facilities",
            "Law and Social Justice",
        ],
    },
    "9": {
        "Mathematics": [
            "Number Systems", "Polynomials", "Coordinate Geometry", "Linear Equations in Two Variables",
            "Introduction to Euclid's Geometry", "Lines and Angles", "Triangles", "Quadrilaterals",
            "Areas of Parallelograms and Triangles", "Circles", "Constructions", "Heron's Formula",
            "Surface Areas and Volumes", "Statistics", "Probability",
        ],
        "Science": [
            "Matter in Our Surroundings", "Is Matter Around Us Pure", "Atoms and Molecules",
            "Structure of the Atom", "The Fundamental Unit of Life", "Tissues",
            "Diversity in Living Organisms", "Motion", "Force and Laws of Motion", "Gravitation",
            "Work and Energy", "Sound", "Why Do We Fall Ill", "Natural Resources",
            "Improvement in Food Resources",
        ],
        "History": [
            "The French Revolution", "Socialism in Europe and the Russian Revolution",
            "Nazism and the Rise of Hitler", "Forest Society and Colonialism",
            "Pastoralists in the Modern World",
        ],
        "Geography": [
            "India - Size and Location", "Physical Features of India", "Drainage", "Climate",
            "Natural Vegetation and Wildlife", "Population",
        ],
        "Political Science": [
            "What is Democracy? Why Democracy?", "Constitutional Design", "Electoral Politics",
            "Working of Institutions", "Democratic Rights",
        ],
        "Economics": [
            "The Story of Village Palampur", "People as Resource", "Poverty as a Challenge",
            "Food Security in India",
        ],
    },
    "10": {
        "Mathematics": [
            "Real Numbers", "Polynomials", "Pair of Linear Equations in Two Variables",
            "Quadratic Equations", "Arithmetic Progressions", "Triangles", "Coordinate Geometry",
            "Introduction to Trigonometry", "Some Applications of Trigonometry", "Circles",
            "Areas Related to Circles", "Surface Areas and Volumes", "Statistics", "Probability",
        ],
        "Science": [
            "Chemical Reactions and Equations", "Acids, Bases and Salts", "Metals and Non-metals",
            "Carbon and its Compounds", "Periodic Classification of Elements", "Life Processes",
            "Control and Coordination", "How do Organisms Reproduce?", "Heredity and Evolution",
            "Light - Reflection and Refraction", "The Human Eye and the Colourful World",
            "Electricity", "Magnetic Effects of Electric Current", "Sources of Energy",
            "Our Environment", "Management of Natural Resources",
        ],
        "History": [
            "The Rise of Nationalism in Europe", "Nationalism in India", "The Making of a Global World",
            "The Age of Industrialisation", "Print Culture and the Modern World",
        ],
        "Geography": [
            "Resources and Development", "Forest and Wildlife Resources", "Water Resources",
            "Agriculture", "Minerals and Energy Resources", "Manufacturing Industries",
            "Lifelines of National Economy",
        ],
        "Political Science": [
            "Power Sharing", "Federalism", "Democracy and Diversity", "Gender, Religion and Caste",
            "Popular Struggles and Movements", "Political Parties", "Outcomes of Democracy",
        ],
        "Economics": [
            "Development", "Sectors of the Indian Economy", "Money and Credit",
            "Globalisation and the Indian Economy", "Consumer Rights",
        ],
    },
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

# Classes 1-5: subject-level only, no chapter detail (see module docstring for why).
PRIMARY_SUBJECTS = ["Mathematics", "English", "Hindi"]


def seed():
    db = SessionLocal()
    created_subjects = created_chapters = 0
    try:
        for class_num in [str(n) for n in range(1, 6)]:
            for subject_name in PRIMARY_SUBJECTS + (["EVS"] if class_num in ("3", "4", "5") else []):
                existing = db.query(Subject).filter(Subject.class_ == class_num, Subject.name == subject_name, Subject.board == "CBSE").first()
                if not existing:
                    db.add(Subject(name=subject_name, class_=class_num, board="CBSE"))
                    created_subjects += 1
        db.commit()

        for class_num, subjects in CURRICULUM.items():
            for subject_name, chapters in subjects.items():
                subject = db.query(Subject).filter(Subject.class_ == class_num, Subject.name == subject_name, Subject.board == "CBSE").first()
                if not subject:
                    subject = Subject(name=subject_name, class_=class_num, board="CBSE")
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
        print(f"Done. Created {created_subjects} new subjects and {created_chapters} new chapters.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
