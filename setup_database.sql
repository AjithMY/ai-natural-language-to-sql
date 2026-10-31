-- ============================================================
-- Sample College Database Setup Script
-- Purpose: Create a test database for Phase 1 schema inspection
-- ============================================================

-- Create the database
CREATE DATABASE IF NOT EXISTS minor_project;
USE minor_project;

-- --------------------------------------------------------
-- Table: departments
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS departments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- --------------------------------------------------------
-- Table: students
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS students (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE,
    marks INT DEFAULT NULL,
    department_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_student_department
        FOREIGN KEY (department_id) REFERENCES departments(id)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB;

-- --------------------------------------------------------
-- Table: courses
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS courses (
    id INT AUTO_INCREMENT PRIMARY KEY,
    course_name VARCHAR(150) NOT NULL,
    credits INT NOT NULL DEFAULT 3,
    department_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_course_department
        FOREIGN KEY (department_id) REFERENCES departments(id)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB;

-- --------------------------------------------------------
-- Table: enrollments (junction table for students <-> courses)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS enrollments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    student_id INT NOT NULL,
    course_id INT NOT NULL,
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_student_course (student_id, course_id),
    CONSTRAINT fk_enrollment_student
        FOREIGN KEY (student_id) REFERENCES students(id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_enrollment_course
        FOREIGN KEY (course_id) REFERENCES courses(id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB;

-- --------------------------------------------------------
-- Sample Data (minimal, for verification only)
-- --------------------------------------------------------
INSERT IGNORE INTO departments (id, name) VALUES
    (1, 'Computer Science'),
    (2, 'Mathematics'),
    (3, 'Physics');

INSERT IGNORE INTO students (id, name, email, marks, department_id) VALUES
    (1, 'Alice Johnson', 'alice@example.com', 85, 1),
    (2, 'Bob Smith', 'bob@example.com', 72, 1),
    (3, 'Charlie Brown', 'charlie@example.com', 91, 2);

INSERT IGNORE INTO courses (id, course_name, credits, department_id) VALUES
    (1, 'Data Structures', 4, 1),
    (2, 'Calculus I', 3, 2),
    (3, 'Quantum Mechanics', 4, 3);

INSERT IGNORE INTO enrollments (student_id, course_id) VALUES
    (1, 1),
    (1, 2),
    (2, 1),
    (3, 2),
    (3, 3);
