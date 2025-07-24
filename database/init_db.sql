CREATE DATABASE staff_db;
USE staff_db;

CREATE TABLE staff (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    position VARCHAR(100),
    total_leaves_taken INT DEFAULT 0,
    total_night_shifts INT DEFAULT 0
);

CREATE TABLE shifts (
    id INT PRIMARY KEY AUTO_INCREMENT,
    staff_id INT,
    date DATE,
    start_time TIME,
    end_time TIME,
    is_night_shift BOOLEAN,
    FOREIGN KEY (staff_id) REFERENCES staff(id)
);

CREATE TABLE leave_requests (
    id INT PRIMARY KEY AUTO_INCREMENT,
    staff_id INT,
    date DATE,
    status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending',
    FOREIGN KEY (staff_id) REFERENCES staff(id)
);
ALTER TABLE shifts
ADD UNIQUE KEY unique_shift_per_day (staff_id, date);

CREATE TABLE leaves (
    id INT AUTO_INCREMENT PRIMARY KEY,
    staff_id INT NOT NULL,
    date DATE NOT NULL,
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);
ALTER TABLE staff
ADD COLUMN total_leaves INT DEFAULT 0,
ADD COLUMN total_night_shifts INT DEFAULT 0;
