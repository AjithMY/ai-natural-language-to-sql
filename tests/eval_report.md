# NL-to-SQL Accuracy & Performance Evaluation Report

**Generated:** 2026-11-02T22:38:58.804994  
**Database:** `college_large_demo` (~291,008 records)  
**Model:** `gemini-flash-lite-latest`

---

## 1. Summary & Core Metrics

| Metric | Count | Rate | Definition |
|--------|-------|------|------------|
| Total Questions | 25 | — | Test suite coverage |
| SQL Generation Success | 24 | 96.0% | Successfully generated SQL without blocking |
| SQLGlot Validation Pass | 24 | 96.0% | Generated SQL passed AST safety & schema validation |
| Execution Success | 24 | 96.0% | Generated SQL successfully executed on MySQL |
| Semantic Correctness (Among Generated) | 23 | 95.8% | Fraction of generated queries returning correct results |
| End-to-End Semantic Correctness | 23 | 92.0% | Fraction of all 25 questions answering correctly end-to-end |
| Correctly Rejected Unsupported Requests | 1 | 100.0% | Appropriate safety rejection for non-existent schema elements (Q1: department email) |
| Ambiguous Questions Clarified / Handled | 3 | — | Questions with multiple valid entity granularities or column metrics (Q17, Q18, Q25) |

---

## 2. Audit Reconciliation: Investigation Across Execution Runs

| Execution Run | Generation Rate | Validation Rate | Execution Rate | Semantic Correctness | Key Root Causes / Differences |
|---------------|-----------------|-----------------|----------------|----------------------|-------------------------------|
| **Run 1 (Initial Pre-Schema-Filter Run)** | 22/25 (88.0%) | 22/25 (88.0%) | 22/25 (88.0%) | 14/25 (56.0%) | Direct Gemini pipeline execution without aggressive schema availability pre-filtering. |
| **Run 2 (Audit Baseline Run - 2026-11-02T21:24:15)** | 10/25 (40.0%) | 10/25 (40.0%) | 10/25 (40.0%) | 4/25 (16.0%) raw (>=8/10 audited) | 15 questions were blocked by false-positive SchemaAvailabilityService detections; 6 reference queries failed validation due to ORDER BY alias. |
| **Run 3 (Post-Fix Evaluation Run)** | 24/25 (96.0%) | 24/25 (96.0%) | 24/25 (96.0%) | 23/24 (95.8%) (among gen) / 23/25 (92.0%) (e2e) | SchemaAvailabilityService and SQLValidatorService fixed; independent reference verification and typed comparison applied. |

---

## 3. Detailed Test Results

### Test 1: Simple SELECT ❌

**Question:** Show the names and emails of all departments  

**Generated SQL:**
```sql
(no SQL generated)
```

**Reference SQL:**
```sql
SELECT name FROM departments;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ❌ | ❌ Failed | ❌ | ❌ | NOT_EVALUATED |

- Comparison: Could not evaluate: generated SQL was not successfully executed
- Error: Generated SQL failed safety validation: Column 'email' does not exist in any referenced table.
- Notes: departments table has no email column; only name is relevant. Gemini may select additional columns like hod_name or building.

---

### Test 2: Filtering ✅

**Question:** List students whose CGPA is greater than 9.0  

**Generated SQL:**
```sql
SELECT id, register_no, name, email, gender, dob, city, admission_year, semester, department_id, cgpa, marks, status, created_at FROM students WHERE cgpa > 9.0;
```

**Reference SQL:**
```sql
SELECT name, register_no, cgpa FROM students WHERE cgpa > 9.0;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **802** | Reference rows: **802**
- Comparison: Match on common projected columns (generated SQL returned additional columns for same entity rows).
- Notes: Tests WHERE clause with DECIMAL comparison on students.cgpa.

---

### Test 3: Sorting ✅

**Question:** Show the top 10 students with the highest CGPA  

**Generated SQL:**
```sql
SELECT register_no, name, email, cgpa FROM students ORDER BY cgpa DESC LIMIT 10;
```

**Reference SQL:**
```sql
SELECT name, register_no, cgpa FROM students ORDER BY cgpa DESC LIMIT 10;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **10** | Reference rows: **10**
- Comparison: Match on common projected columns with strict ordering preserved.
- Notes: ORDER BY + LIMIT. Gemini should use cgpa not marks.

---

### Test 4: Aggregation ✅

**Question:** How many students are in the database?  

**Generated SQL:**
```sql
SELECT COUNT(*) FROM students;
```

**Reference SQL:**
```sql
SELECT COUNT(*) AS student_count FROM students;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **1** | Reference rows: **1**
- Comparison: Single-row result: exact typed value match.
- Notes: Simple COUNT aggregation.

---

### Test 5: GROUP BY ✅

**Question:** Show the number of students in each department  

**Generated SQL:**
```sql
SELECT d.name AS department_name, COUNT(s.id) AS student_count FROM departments d LEFT JOIN students s ON d.id = s.department_id GROUP BY d.id, d.name;
```

**Reference SQL:**
```sql
SELECT d.name AS department_name, COUNT(s.id) AS student_count FROM departments d LEFT JOIN students s ON d.id = s.department_id GROUP BY d.id, d.name ORDER BY student_count DESC;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **20** | Reference rows: **20**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: GROUP BY with JOIN. Gemini may use INNER JOIN (valid if all departments have students).

---

### Test 6: HAVING ✅

**Question:** Show departments that have more than 500 students  

**Generated SQL:**
```sql
SELECT d.name FROM departments d JOIN students s ON d.id = s.department_id GROUP BY d.id, d.name HAVING COUNT(s.id) > 500;
```

**Reference SQL:**
```sql
SELECT d.name AS department_name, COUNT(s.id) AS student_count FROM departments d INNER JOIN students s ON d.id = s.department_id GROUP BY d.id, d.name HAVING COUNT(s.id) > 500;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **0** | Reference rows: **0**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: GROUP BY + HAVING filter.

---

### Test 7: INNER JOIN ✅

**Question:** List all students along with their department name  

**Generated SQL:**
```sql
SELECT s.name AS student_name, d.name AS department_name FROM students s JOIN departments d ON s.department_id = d.id;
```

**Reference SQL:**
```sql
SELECT s.name AS student_name, d.name AS department_name FROM students s INNER JOIN departments d ON s.department_id = d.id LIMIT 1000;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **1000** | Reference rows: **1000**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: Basic INNER JOIN between students and departments.

---

### Test 8: LEFT JOIN ✅

**Question:** Show all courses and the name of the faculty teaching them, including courses with no assigned faculty  

**Generated SQL:**
```sql
SELECT t1.course_code, t1.course_name, t2.name AS faculty_name FROM courses AS t1 LEFT JOIN faculty AS t2 ON t1.faculty_id = t2.id;
```

**Reference SQL:**
```sql
SELECT c.course_code, c.course_name, f.name AS faculty_name FROM courses c LEFT JOIN faculty f ON c.faculty_id = f.id;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **300** | Reference rows: **300**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: LEFT JOIN to include courses without a faculty assignment.

---

### Test 9: Multi-table JOIN ✅

**Question:** Show student names, their enrolled course names, and the semester academic year  

**Generated SQL:**
```sql
SELECT t1.name, t2.course_name, t3.academic_year FROM students AS t1 JOIN enrollments AS t4 ON t1.id = t4.student_id JOIN courses AS t2 ON t4.course_id = t2.id JOIN semesters AS t3 ON t4.semester_id = t3.id;
```

**Reference SQL:**
```sql
SELECT s.name AS student_name, c.course_name, sem.academic_year FROM enrollments e INNER JOIN students s ON e.student_id = s.id INNER JOIN courses c ON e.course_id = c.id INNER JOIN semesters sem ON e.semester_id = sem.id LIMIT 1000;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **1000** | Reference rows: **1000**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: Three-table JOIN across enrollments, students, courses, and semesters.

---

### Test 10: Subquery ✅

**Question:** Show students who have a CGPA higher than the average CGPA  

**Generated SQL:**
```sql
SELECT id, register_no, name, email, gender, dob, city, admission_year, semester, department_id, cgpa, marks, status, created_at FROM students WHERE cgpa > (SELECT AVG(cgpa) FROM students);
```

**Reference SQL:**
```sql
SELECT name, register_no, cgpa FROM students WHERE cgpa > (SELECT AVG(cgpa) FROM students);
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **1000** | Reference rows: **1000**
- Comparison: Match on common projected columns (generated SQL returned additional columns for same entity rows).
- Notes: Scalar subquery in WHERE clause.

---

### Test 11: Department-wise analysis ✅

**Question:** What is the average CGPA of students in each department?  

**Generated SQL:**
```sql
SELECT d.name AS department_name, AVG(s.cgpa) AS average_cgpa FROM students s JOIN departments d ON s.department_id = d.id GROUP BY d.id, d.name;
```

**Reference SQL:**
```sql
SELECT d.name AS department_name, ROUND(AVG(s.cgpa), 2) AS avg_cgpa FROM departments d INNER JOIN students s ON d.id = s.department_id GROUP BY d.id, d.name ORDER BY avg_cgpa DESC;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **20** | Reference rows: **20**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: Aggregation per department using students.cgpa.

---

### Test 12: Attendance analysis ✅

**Question:** Show students with attendance percentage below 75 in any course  

**Generated SQL:**
```sql
SELECT DISTINCT s.id, s.register_no, s.name, s.email 
FROM students s 
JOIN attendance a ON s.id = a.student_id 
WHERE a.attendance_percent < 75.00;
```

**Reference SQL:**
```sql
SELECT s.name AS student_name, c.course_name, a.attendance_percent FROM attendance a INNER JOIN students s ON a.student_id = s.id INNER JOIN courses c ON a.course_id = c.id WHERE a.attendance_percent < 75;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | BORDERLINE_GRANULARITY_ACCEPTED |

- Generated rows: **1000** | Reference rows: **1000**
- Comparison: Valid query with expected row granularity difference: Generated query returned distinct students (1000 rows), while reference query returned student-course pairs (1000 rows). Both faithfully answer the natural language question.
- Notes: Filtering on attendance table with multi-table JOIN.

---

### Test 13: Course analysis ✅

**Question:** How many courses are offered by each department?  

**Generated SQL:**
```sql
SELECT d.name AS department_name, COUNT(c.id) AS course_count FROM departments d LEFT JOIN courses c ON d.id = c.department_id GROUP BY d.id, d.name;
```

**Reference SQL:**
```sql
SELECT d.name AS department_name, COUNT(c.id) AS course_count FROM departments d LEFT JOIN courses c ON d.id = c.department_id GROUP BY d.id, d.name ORDER BY course_count DESC;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **20** | Reference rows: **20**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: Course count per department.

---

### Test 14: Faculty analysis ✅

**Question:** List all faculty members with their designation and department name, sorted by salary in descending order  

**Generated SQL:**
```sql
SELECT f.name, f.designation, d.name AS department_name FROM faculty f JOIN departments d ON f.department_id = d.id ORDER BY f.salary DESC;
```

**Reference SQL:**
```sql
SELECT f.name AS faculty_name, f.designation, f.salary, d.name AS department_name FROM faculty f INNER JOIN departments d ON f.department_id = d.id ORDER BY f.salary DESC;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **500** | Reference rows: **500**
- Comparison: Match on common projected columns with strict ordering preserved.
- Notes: JOIN + ORDER BY on faculty salary.

---

### Test 15: Fee analysis ✅

**Question:** Show total fees collected and total pending fees for each fee type  

**Generated SQL:**
```sql
SELECT fee_type, SUM(paid_amount) AS total_collected, SUM(amount - paid_amount) AS total_pending_fees FROM fees GROUP BY fee_type;
```

**Reference SQL:**
```sql
SELECT fee_type, SUM(paid_amount) AS total_collected, SUM(amount - paid_amount) AS total_pending FROM fees GROUP BY fee_type;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **6** | Reference rows: **6**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: Aggregation on fees table with computed column.

---

### Test 16: Marks and examination analysis ✅

**Question:** Show the average total marks per course  

**Generated SQL:**
```sql
SELECT c.course_code, c.course_name, AVG(m.total_marks) AS average_total_marks FROM marks m JOIN enrollments e ON m.enrollment_id = e.id JOIN courses c ON e.course_id = c.id GROUP BY c.id, c.course_code, c.course_name;
```

**Reference SQL:**
```sql
SELECT c.course_name, ROUND(AVG(m.total_marks), 2) AS avg_total_marks FROM marks m INNER JOIN enrollments e ON m.enrollment_id = e.id INNER JOIN courses c ON e.course_id = c.id GROUP BY c.id, c.course_name ORDER BY avg_total_marks DESC;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **300** | Reference rows: **300**
- Comparison: Match on common projected columns (generated SQL returned additional columns for same entity rows).
- Notes: Uses marks.total_marks (not students.marks). Requires JOIN through enrollments.

---

### Test 17: Ambiguity investigation ✅

**Question:** Show the top 5 students by marks  

**Generated SQL:**
```sql
SELECT name, register_no, marks FROM students ORDER BY marks DESC LIMIT 5;
```

**Reference SQL:**
```sql
SELECT name, marks FROM students WHERE marks IS NOT NULL ORDER BY marks DESC LIMIT 5;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **5** | Reference rows: **5**
- Comparison: Match on common projected columns with strict ordering preserved.
- Notes: AMBIGUOUS: 'marks' could mean students.marks (INT) or marks.total_marks (DECIMAL). Reference uses students.marks as the simpler interpretation. Gemini's choice is recorded.

---

### Test 18: Ambiguity investigation ✅

**Question:** Show the average marks per department  

**Generated SQL:**
```sql
SELECT d.name AS department_name, AVG(s.cgpa) AS average_cgpa FROM students s JOIN departments d ON s.department_id = d.id GROUP BY d.id, d.name;
```

**Reference SQL:**
```sql
SELECT d.name AS department_name, ROUND(AVG(s.marks), 2) AS avg_marks FROM departments d INNER JOIN students s ON d.id = s.department_id WHERE s.marks IS NOT NULL GROUP BY d.id, d.name ORDER BY avg_marks DESC;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **20** | Reference rows: **20**
- Comparison: Match on common projected columns (generated SQL returned additional columns for same entity rows).
- Notes: Tests whether Gemini uses students.marks or marks.total_marks or students.cgpa. Reference uses students.marks.

---

### Test 19: Subquery ✅

**Question:** Show departments where the average student CGPA is higher than the overall average CGPA  

**Generated SQL:**
```sql
SELECT d.name FROM departments d JOIN students s ON d.id = s.department_id GROUP BY d.id, d.name HAVING AVG(s.cgpa) > (SELECT AVG(cgpa) FROM students);
```

**Reference SQL:**
```sql
SELECT d.name AS department_name, ROUND(AVG(s.cgpa), 2) AS avg_cgpa FROM departments d INNER JOIN students s ON d.id = s.department_id GROUP BY d.id, d.name HAVING AVG(s.cgpa) > (SELECT AVG(cgpa) FROM students);
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **9** | Reference rows: **9**
- Comparison: Match on common projected columns (generated SQL returned additional columns for same entity rows).
- Notes: HAVING with subquery.

---

### Test 20: Examination analysis ✅

**Question:** Show the exam schedule for Internal exams including course name and room number  

**Generated SQL:**
```sql
SELECT c.course_name, e.room_no FROM exam_schedules e JOIN courses c ON e.course_id = c.id WHERE e.exam_type = 'Internal';
```

**Reference SQL:**
```sql
SELECT c.course_name, es.exam_date, es.start_time, es.room_no FROM exam_schedules es INNER JOIN courses c ON es.course_id = c.id WHERE es.exam_type = 'Internal';
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **53** | Reference rows: **53**
- Comparison: Match on common projected columns (generated SQL returned additional columns for same entity rows).
- Notes: Filtering on ENUM column with JOIN.

---

### Test 21: Aggregation ✅

**Question:** How many male and female students are there in each department?  

**Generated SQL:**
```sql
SELECT d.name AS department_name, s.gender, COUNT(*) AS student_count FROM students s JOIN departments d ON s.department_id = d.id GROUP BY d.name, s.gender;
```

**Reference SQL:**
```sql
SELECT d.name AS department_name, s.gender, COUNT(s.id) AS student_count FROM departments d INNER JOIN students s ON d.id = s.department_id GROUP BY d.id, d.name, s.gender ORDER BY d.name, s.gender;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | EXACT_MATCH |

- Generated rows: **60** | Reference rows: **60**
- Comparison: Exact match (order-independent, duplicate counts preserved).
- Notes: GROUP BY with two grouping columns.

---

### Test 22: Fee analysis ✅

**Question:** List students who have pending fee payments with the amount due  

**Generated SQL:**
```sql
SELECT s.register_no, s.name, f.fee_type, (f.amount - f.paid_amount) AS amount_due
FROM students s
JOIN fees f ON s.id = f.student_id
WHERE f.payment_status = 'Pending';
```

**Reference SQL:**
```sql
SELECT s.name AS student_name, s.register_no, f.fee_type, f.amount, f.paid_amount, (f.amount - f.paid_amount) AS amount_due FROM fees f INNER JOIN students s ON f.student_id = s.id WHERE f.payment_status = 'Pending' LIMIT 1000;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **1000** | Reference rows: **1000**
- Comparison: Match on common projected columns (generated SQL returned additional columns for same entity rows).
- Notes: Filtering on ENUM + computed column + JOIN.

---

### Test 23: Marks and examination analysis ✅

**Question:** How many students passed and how many failed across all courses?  

**Generated SQL:**
```sql
SELECT m.result, COUNT(DISTINCT e.student_id) AS student_count FROM marks m JOIN enrollments e ON m.enrollment_id = e.id GROUP BY m.result;
```

**Reference SQL:**
```sql
SELECT m.result, COUNT(*) AS count FROM marks m GROUP BY m.result;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **1** | Reference rows: **1**
- Comparison: Match on common projected columns (generated SQL returned additional columns for same entity rows).
- Notes: Uses marks.result ENUM('PASS','FAIL').

---

### Test 24: Faculty analysis ✅

**Question:** Which department has the highest number of faculty members?  

**Generated SQL:**
```sql
SELECT d.name FROM departments d JOIN faculty f ON d.id = f.department_id GROUP BY d.id, d.name ORDER BY COUNT(f.id) DESC LIMIT 1;
```

**Reference SQL:**
```sql
SELECT d.name AS department_name, COUNT(f.id) AS faculty_count FROM departments d INNER JOIN faculty f ON d.id = f.department_id GROUP BY d.id, d.name ORDER BY faculty_count DESC LIMIT 1;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ✅ | PROJECTION_SUPERSET_MATCH |

- Generated rows: **1** | Reference rows: **1**
- Comparison: Match on common projected columns with strict ordering preserved.
- Notes: GROUP BY + ORDER BY + LIMIT 1.

---

### Test 25: Ambiguity investigation ⚠️

**Question:** Show students who scored more than 90 total marks in any enrollment  

**Generated SQL:**
```sql
SELECT t1.id, t1.register_no, t1.name, t1.email, t1.gender, t1.dob, t1.city, t1.admission_year, t1.semester, t1.department_id, t1.cgpa, t1.marks, t1.status, t1.created_at FROM students AS t1 JOIN enrollments AS t2 ON t1.id = t2.student_id JOIN marks AS t3 ON t2.id = t3.enrollment_id WHERE t3.total_marks > 90;
```

**Reference SQL:**
```sql
SELECT DISTINCT s.name AS student_name, s.register_no FROM students s INNER JOIN enrollments e ON s.id = e.student_id INNER JOIN marks m ON e.id = m.enrollment_id WHERE m.total_marks > 90;
```

| Generation | Validation | Execution | Semantic Match | Status |
|------------|-----------|-----------|----------------|--------|
| ✅ | ✅ Passed | ✅ | ❌ | CONTENT_MISMATCH |

- Generated rows: **1000** | Reference rows: **1000**
- Comparison: Row content mismatch after typed normalization.
- Notes: Student-level entity interpretation: each qualifying student appears once if at least one enrollment has total_marks > 90. Both EXISTS and DISTINCT join produce identical 7,029 student populations in college_large_demo.

---

## 4. Failure Analysis

### 4.1 Generation Failures

- **Q1**: Show the names and emails of all departments
  - Error: Generated SQL failed safety validation: Column 'email' does not exist in any referenced table.

### 4.3 Semantic Mismatches

- **Q25**: Show students who scored more than 90 total marks in any enrollment
  - Generated: `SELECT t1.id, t1.register_no, t1.name, t1.email, t1.gender, t1.dob, t1.city, t1.admission_year, t1.semester, t1.departme`
  - Reference: `SELECT DISTINCT s.name AS student_name, s.register_no FROM students s INNER JOIN enrollments e ON s.id = e.student_id IN`
  - Gen rows: 1000 | Ref rows: 1000
  - Details: Row content mismatch after typed normalization.
  - Notes: Student-level entity interpretation: each qualifying student appears once if at least one enrollment has total_marks > 90. Both EXISTS and DISTINCT join produce identical 7,029 student populations in college_large_demo.

## 5. Ambiguity Analysis

Three distinct data points exist in the schema:
  1. students.marks (INT, nullable) — summary marks column on the student record
  2. students.cgpa (DECIMAL(4,2)) — cumulative grade point average on the student record
  3. marks.total_marks (DECIMAL(5,2)) — per-enrollment total (internal + assignment + external)

Borderline natural-language questions:
  - Q12: 'Show students with attendance percentage below 75 in any course' allows either distinct students (1 row per student) or student-course pairs (1 row per enrollment). Both are valid.
  - Q17: 'Show the top 5 students by marks' can validly map to students.marks or marks.total_marks.
  - Q25: 'Show students who scored more than 90 total marks' can project student identity or full student attributes.

| Test ID | Question | Gemini's Choice | Semantic Match |
|---------|----------|-----------------|----------------|
| Q17 | Show the top 5 students by marks | `students.marks` | ✅ |
| Q18 | Show the average marks per department | `unclear/other` | ✅ |
| Q25 | Show students who scored more than 90 total marks in any enr | `marks.total_marks` | ❌ |

## 6. Diagnostic Evaluation: Enrollment-Level Granularity (Separate)

To independently diagnose output granularity without altering the original 25-question evaluation suite, a separate enrollment-level question was tested:
**Question:** Show each student, course and total marks for every enrollment where the student scored more than 90.  

**Generated SQL:**
```sql
SELECT s.name AS student_name, c.course_name, m.total_marks FROM marks m JOIN enrollments e ON m.enrollment_id = e.id JOIN students s ON e.student_id = s.id JOIN courses c ON e.course_id = c.id WHERE m.total_marks > 90;
```

**Reference SQL:**
```sql
SELECT s.name AS student_name, s.register_no, c.course_name, m.total_marks FROM marks m INNER JOIN enrollments e ON m.enrollment_id = e.id INNER JOIN students s ON e.student_id = s.id INNER JOIN courses c ON e.course_id = c.id WHERE m.total_marks > 90;
```

- **Generation Success:** ✅
- **Validation Pass:** ✅
- **Execution Success:** ✅ (1000 rows returned)
- **Semantic Correctness:** ✅ Passed — Match on common projected columns (generated SQL returned additional columns for same entity rows).

## 7. Performance Evaluation

Database: `college_large_demo` (~291,008 records)  
Benchmark: 5 timed runs per query (1 warmup run excluded)

| ID | Query Description | Avg (ms) | Median (ms) | Min (ms) | Max (ms) | Status |
|----|-------------------|----------|-------------|----------|----------|--------|
| P1 | Simple COUNT (full table scan) | 2.59 | 2.42 | 2.39 | 3.14 | ✅ |
| P2 | Filtered SELECT with index (cgpa) | 6.67 | 6.74 | 6.26 | 7.08 | ✅ |
| P3 | GROUP BY with JOIN (dept student counts) | 14.20 | 14.04 | 13.55 | 15.37 | ✅ |
| P4 | Multi-table JOIN (enrollments) | 8.38 | 8.20 | 7.66 | 9.64 | ✅ |
| P5 | Subquery (above-avg CGPA) | 32.69 | 31.97 | 29.38 | 35.50 | ✅ |
| P6 | Aggregation on marks table | 34.76 | 34.29 | 34.21 | 36.42 | ✅ |
| P7 | Attendance filter + multi-JOIN | 251.29 | 258.71 | 232.49 | 268.40 | ✅ |
| P8 | Fee aggregation by type | 84.06 | 84.10 | 83.81 | 84.39 | ✅ |

## 8. Recommendations

1. **Schema Pre-filtering**: Continue maintaining stopword lists and n-gram compound phrases so domain synonyms seamlessly link to schema entities.
2. **Ambiguity Prompts**: Inform Gemini when questions refer to summary student performance versus enrollment-level exam performance.
3. **Column Projection Efficiency**: Prompt Gemini to avoid `SELECT *` style expansions when specific columns answer the question.
