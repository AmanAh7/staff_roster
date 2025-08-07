from flask import Flask, flash, render_template, request, redirect, url_for, send_file
import pymysql
pymysql.install_as_MySQLdb()
from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from openpyxl import Workbook
from io import BytesIO
from collections import defaultdict
from dotenv import load_dotenv
import os
import pytz

india = pytz.timezone("Asia/Kolkata")

load_dotenv()

app = Flask(__name__)  # fix here (name_)
app.config.from_pyfile('config.py')
app.secret_key = 'abcd1234'

# Database connection function
def get_db_connection():
    return pymysql.connect(
        host=app.config['MYSQL_HOST'],
        port=app.config['MYSQL_PORT'],
        user=app.config['MYSQL_USER'],
        password=app.config['MYSQL_PASSWORD'],
        database=app.config['MYSQL_DB'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False
    )

# Utility functions
def is_night_shift(start_time_str: str, end_time_str: str) -> bool:
    try:
        st = datetime.strptime(start_time_str, "%H:%M:%S").time()
    except ValueError:
        st = datetime.strptime(start_time_str, "%H:%M").time()
    return st >= time(17, 0) or st < time(2, 0)

def to_time(val):
    if isinstance(val, time):
        return val
    elif isinstance(val, timedelta):
        total_seconds = int(val.total_seconds())
        hours = (total_seconds // 3600) % 24
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return time(hours, minutes, seconds)
    return val

def check_overlap(staff_id, date, new_start, new_end):
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT start_time, end_time FROM shifts
                WHERE staff_id = %s AND date = %s
            """, (staff_id, date))
            overlaps = []
            for row in cur.fetchall():
                os_, oe = row['start_time'], row['end_time']
                if not (new_end <= os_ or new_start >= oe):
                    overlaps.append((os_, oe))
            return overlaps
    finally:
        connection.close()

def get_today():
    # Utility to get today's date in Asia/Kolkata timezone
    return datetime.now(india).date()

@app.route('/')
def dashboard():
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT id, name, position, total_leaves, total_night_shifts FROM staff")
            staff = cur.fetchall()
            today = get_today()
            shift_data = {}
            for s in staff:
                cur.execute(
                    "SELECT id, date, start_time, end_time FROM shifts WHERE staff_id=%s AND date=%s ORDER BY start_time", 
                    (s["id"], today)
                )
                shift_data[s["id"]] = cur.fetchall()
            return render_template('dashboard.html', staff=staff, shift_data=shift_data)
    finally:
        connection.close()

@app.route('/assign-shift', methods=['GET', 'POST'])
def assign_shift():
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            if request.method == 'POST':
                staff_id = request.form['staff_id']
                dates = request.form.getlist('date[]')
                start_times = request.form.getlist('start_time[]')
                end_times = request.form.getlist('end_time[]')

                for i in range(len(dates)):
                    date_str = dates[i]
                    start_str = start_times[i]
                    end_str = end_times[i]

                    try:
                        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                        start_time = datetime.strptime(start_str, "%H:%M").time()
                        end_time = datetime.strptime(end_str, "%H:%M").time()
                    except ValueError:
                        flash(f"⚠ Invalid time format for shift on {date_str}.", "danger")
                        continue

                    start_dt = datetime.combine(date_obj, start_time)
                    end_dt = datetime.combine(date_obj, end_time)

                    # Overlap check
                    cur.execute("""
                        SELECT start_time, end_time
                        FROM shifts
                        WHERE staff_id = %s AND date = %s
                    """, (staff_id, date_str))
                    overlaps = []
                    for row in cur.fetchall():
                        existing_start, existing_end = row['start_time'], row['end_time']
                        existing_start_dt = datetime.combine(date_obj, to_time(existing_start))
                        existing_end_dt = datetime.combine(date_obj, to_time(existing_end))
                        if start_dt < existing_end_dt and end_dt > existing_start_dt:
                            overlaps.append(f"{existing_start_dt.time().strftime('%H:%M')} - {existing_end_dt.time().strftime('%H:%M')}")

                    if overlaps:
                        flash(f"❌ Overlap on {date_str} with: {', '.join(overlaps)}", "danger")
                        continue

                    is_night = is_night_shift(start_str, end_str)
                    if is_night:
                        cur.execute("""
                            SELECT COUNT(*) AS night_count FROM shifts
                            WHERE staff_id = %s AND is_night_shift = 1 AND MONTH(date) = MONTH(CURDATE())
                        """, (staff_id,))
                        result = cur.fetchone()
                        if result['night_count'] >= 8:  
                            flash(f"⚠ Night shift limit reached for staff ID {staff_id}.", "warning")
                            continue

                    cur.execute("""
                        INSERT INTO shifts (staff_id, date, start_time, end_time, is_night_shift)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (staff_id, date_str, start_str, end_str, int(is_night)))

                    if is_night:
                        cur.execute("""
                            UPDATE staff SET total_night_shifts = total_night_shifts + 1 WHERE id = %s
                        """, (staff_id,))

                connection.commit()
                return redirect(url_for('assign_shift'))

            # GET: load form and weekly shift table
            cur.execute("SELECT id, name FROM staff")
            staff = cur.fetchall()

            today = get_today()

            # Show the current week starting Monday, automatically including Monday
            if today.weekday() == 0:
                start_of_week = today
            else:
                start_of_week = today - timedelta(days=today.weekday())
            end_of_week = start_of_week + timedelta(days=6)

            cur.execute("""
                SELECT sh.id, sh.staff_id, s.name AS staff_name, s.position,
                sh.date, sh.start_time, sh.end_time
                FROM shifts sh
                JOIN staff s ON s.id = sh.staff_id
                ORDER BY sh.date, sh.start_time
                """)

            weekly_shifts = cur.fetchall()

            return render_template(
                'assign_shift.html',
                staff=staff,
                assigned_shifts=weekly_shifts,
                start_of_week=None,
                end_of_week=None
            )

    finally:
        connection.close()

@app.route('/export_leaves', methods=['POST'])
def export_leaves():
    staff_id = request.form.get('staff_id')
    if not staff_id:
        return "No staff selected", 400
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
            row = cur.fetchone()
            if not row:
                return "Staff not found", 404
            staff_name = row['name']
            cur.execute("SELECT date FROM leaves WHERE staff_id = %s ORDER BY date DESC", (staff_id,))
            leave_dates = cur.fetchall()
            
            wb = Workbook()
            ws = wb.active
            ws.title = "Leave History"
            ws.append([f"Leave History for {staff_name}"])
            ws.append([])
            ws.append(['Date'])
            for ld in leave_dates:
                ws.append([ld['date'].strftime("%Y-%m-%d")])
            
            bio = BytesIO()
            wb.save(bio)
            bio.seek(0)
            return send_file(
                bio,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                download_name=f"{staff_name}_leave_history.xlsx",
                as_attachment=True
            )
    finally:
        connection.close()

@app.route('/export-shifts', methods=['POST'])
def export_shifts():
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    selected_ids = request.form.getlist('staff_ids')
    if not (start_date and end_date and selected_ids):
        return "Missing filters", 400
    
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            fmt = ','.join(['%s'] * len(selected_ids))
            cur.execute(f"""
                SELECT s.name, sh.date, sh.start_time, sh.end_time,
                       CASE WHEN sh.is_night_shift=1 THEN 'Yes' ELSE 'No' END as night_shift
                FROM shifts sh JOIN staff s ON s.id=sh.staff_id
                WHERE sh.date BETWEEN %s AND %s AND sh.staff_id IN ({fmt})
                ORDER BY sh.date, s.name
            """, [start_date, end_date] + selected_ids)
            rows = cur.fetchall()
            
            wb = Workbook()
            ws = wb.active
            ws.title = "Shift Report"
            headers = ['Staff', 'Date', 'Start', 'End', 'Night?']
            ws.append(headers)
            for r in rows:
                ws.append([r['name'], r['date'], r['start_time'], r['end_time'], r['night_shift']])
            
            bio = BytesIO()
            wb.save(bio)
            bio.seek(0)
            return send_file(
                bio,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                download_name='shift_report.xlsx',
                as_attachment=True
            )
    finally:
        connection.close()

@app.route('/add-staff', methods=['GET', 'POST'])
def add_staff():
    if request.method == 'POST':
        name = request.form['name']
        position = request.form['position']
        connection = get_db_connection()
        try:
            with connection.cursor() as cur:
                cur.execute("INSERT INTO staff (name, position) VALUES (%s, %s)", (name, position))
                connection.commit()
                return redirect(url_for('dashboard'))
        finally:
            connection.close()
    return render_template('add_staff.html')

@app.route('/shifts-range', methods=['GET'])
def shifts_range():
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT id, name FROM staff")
            all_staff = cur.fetchall()

            staff_ids = request.args.getlist('staff_ids')
            start = request.args.get('start')
            end = request.args.get('end')

            results = {}

            if staff_ids and start and end:
                for staff_id in staff_ids:
                    cur.execute("""
                        SELECT date, start_time, end_time FROM shifts
                        WHERE staff_id = %s AND date BETWEEN %s AND %s
                        ORDER BY date
                    """, (staff_id, start, end))
                    shifts = cur.fetchall()

                    cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
                    name = cur.fetchone()['name']
                    results[name] = shifts

            return render_template('shifts_range.html', staff=all_staff, results=results)
    finally:
        connection.close()

def staff_available(staff_id, date, start_time, end_time):
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) as count FROM shifts
                WHERE staff_id = %s AND date = %s
            """, (staff_id, date))
            count = cur.fetchone()['count']
            return count == 0
    finally:
        connection.close()

@app.route('/delete-shift/<int:shift_id>', methods=['POST'])
def delete_shift(shift_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT staff_id, is_night_shift FROM shifts WHERE id = %s", (shift_id,))
            result = cur.fetchone()
            if not result:
                flash("❌ Shift not found.", "danger")
                return redirect(url_for('assign_shift'))

            staff_id, is_night_shift = result['staff_id'], result['is_night_shift']
            if is_night_shift:
                cur.execute("""
                    UPDATE staff
                    SET total_night_shifts = GREATEST(total_night_shifts - 1, 0)
                    WHERE id = %s
                """, (staff_id,))
            cur.execute("DELETE FROM shifts WHERE id = %s", (shift_id,))
            connection.commit()

            flash("✅ Shift deleted.", "success")
            return redirect(url_for('assign_shift'))
    finally:
        connection.close()

@app.route('/replacement')
def replacement():
    date = request.args.get('date')
    start_time = request.args.get('start_time')
    end_time = request.args.get('end_time')

    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT id, name FROM staff")
            all_staff = cur.fetchall()

            available = []
            for staff in all_staff:
                if staff_available(staff['id'], date, start_time, end_time):
                    available.append(staff)

            return render_template('replacement.html', available=available, date=date, start_time=start_time, end_time=end_time)
    finally:
        connection.close()

@app.route('/apply-leave/<int:staff_id>', methods=['POST'])
def apply_leave(staff_id):
    date = request.form['date']
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) as count FROM leaves
                WHERE staff_id = %s AND date = %s
            """, (staff_id, date))
            existing = cur.fetchone()['count']

            if existing > 0:
                return "❌ Leave already applied for this date."

            cur.execute("""
                SELECT COUNT(*) as count FROM leaves
                WHERE staff_id = %s AND MONTH(date) = MONTH(CURDATE()) AND YEAR(date) = YEAR(CURDATE())
            """, (staff_id,))
            leave_count = cur.fetchone()['count']

            if leave_count >= 4:
                return "❌ Leave limit (4/month) reached."

            cur.execute("INSERT INTO leaves (staff_id, date) VALUES (%s, %s)", (staff_id, date))
            cur.execute("UPDATE staff SET total_leaves = total_leaves + 1 WHERE id = %s", (staff_id,))

            connection.commit()
            return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/apply-leave', methods=['GET', 'POST'])
def apply_leave_form():
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            if request.method == 'POST':
                staff_id = request.form['staff_id']
                date = request.form['date']

                cur.execute("""
                    SELECT COUNT(*) as count FROM leaves
                    WHERE staff_id = %s AND MONTH(date) = MONTH(CURDATE())
                """, (staff_id,))
                leave_count = cur.fetchone()['count']

                if leave_count >= 4:
                    return "❌ Leave limit (4/month) reached."

                cur.execute("INSERT INTO leaves (staff_id, date) VALUES (%s, %s)", (staff_id, date))
                cur.execute("UPDATE staff SET total_leaves = total_leaves + 1 WHERE id = %s", (staff_id,))
                connection.commit()

                return redirect(url_for('dashboard'))

            cur.execute("SELECT id, name FROM staff")
            staff = cur.fetchall()
            return render_template('apply_leave.html', staff=staff)
    finally:
        connection.close()

@app.route('/edit-position/<int:staff_id>', methods=['GET', 'POST'])
def edit_position(staff_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            if request.method == 'POST':
                new_position = request.form['position']
                cur.execute("UPDATE staff SET position = %s WHERE id = %s", (new_position, staff_id))
                connection.commit()
                return redirect(url_for('dashboard'))

            cur.execute("SELECT id, name, position FROM staff WHERE id = %s", (staff_id,))
            staff = cur.fetchone()
            return render_template('edit_position.html', staff=staff)
    finally:
        connection.close()

@app.route('/edit-shift/<int:shift_id>', methods=['GET', 'POST'])
def edit_shift(shift_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            if request.method == 'POST':
                start_time_raw = request.form['start_time']
                end_time_raw = request.form['end_time']

                def parse_time(value: str) -> time:
                    for fmt in ("%H:%M", "%H:%M:%S"):
                        try:
                            return datetime.strptime(value, fmt).time()
                        except ValueError:
                            continue
                    raise ValueError(f"Unrecognized time format: {value}")

                try:
                    start_time = parse_time(start_time_raw)
                    end_time = parse_time(end_time_raw)
                except ValueError as e:
                    flash(str(e), "danger")
                    return redirect(url_for('assign_shift'))

                start_time_str = start_time.strftime("%H:%M:%S")
                end_time_str = end_time.strftime("%H:%M:%S")
                is_night = is_night_shift(start_time_str, end_time_str)

                cur.execute("""
                    UPDATE shifts 
                    SET start_time = %s, end_time = %s, is_night_shift = %s 
                    WHERE id = %s
                """, (start_time_str, end_time_str, int(is_night), shift_id))
                connection.commit()

                flash("✅ Shift updated successfully.", "success")
                return redirect(url_for('assign_shift'))

            # GET: load shift info
            cur.execute("""
                SELECT s.id, st.name, s.date, s.start_time, s.end_time 
                FROM shifts s
                JOIN staff st ON st.id = s.staff_id
                WHERE s.id = %s
            """, (shift_id,))
            shift = cur.fetchone()

            return render_template(
                'edit_shift.html',
                shift=shift,
                start_date=request.args.get('start_date', ''),
                end_date=request.args.get('end_date', ''),
                staff_ids=request.args.getlist('staff_ids')
            )
    finally:
        connection.close()

@app.route('/show-leaves-form', methods=['GET', 'POST'])
def show_leaves_form():
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT id, name FROM staff")
            staff_list = cur.fetchall()

            leave_records = []
            leave_summary = {}
            selected_staff = None
            staff_name = None

            if request.method == 'POST':
                selected_staff = request.form['staff_id']

                cur.execute("SELECT name FROM staff WHERE id = %s", (selected_staff,))
                row = cur.fetchone()
                if row:
                    staff_name = row['name']

                cur.execute("""
                    SELECT date FROM leaves
                    WHERE staff_id = %s ORDER BY date DESC
                """, (selected_staff,))
                leave_records = cur.fetchall()

                summary = defaultdict(int)
                for record in leave_records:
                    leave_date = record['date']
                    month_year = leave_date.strftime("%B %Y")
                    summary[month_year] += 1
                leave_summary = dict(summary)

            return render_template(
                'show_leaves_form.html',
                staff_list=staff_list,
                leave_records=leave_records,
                staff_name=staff_name,
                selected_staff=selected_staff,
                leave_summary=leave_summary
            )
    finally:
        connection.close()

@app.route('/clear-shifts-today', methods=['POST'])
def clear_shifts_today():
    today = get_today()
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT staff_id, COUNT(*) as count FROM shifts
                WHERE date = %s AND is_night_shift = 1
                GROUP BY staff_id
            """, (today,))
            night_shift_counts = cur.fetchall()
            for record in night_shift_counts:
                staff_id, count = record['staff_id'], record['count']
                cur.execute("""
                    UPDATE staff SET total_night_shifts = GREATEST(total_night_shifts - %s, 0)
                    WHERE id = %s
                """, (count, staff_id))

            cur.execute("DELETE FROM shifts WHERE date = %s", (today,))
            connection.commit()

            return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/clear-leaves', methods=['POST'])
def clear_leaves():
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("UPDATE staff SET total_leaves = 0")
            connection.commit()
            return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/clear-night-shifts', methods=['POST'])
def clear_night_shifts():
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("UPDATE staff SET total_night_shifts = 0")
            connection.commit()
            return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/clear-night-shifts/<int:staff_id>', methods=['POST'])
def clear_individual_night_shifts(staff_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("UPDATE staff SET total_night_shifts = 0 WHERE id = %s", (staff_id,))
            connection.commit()
            return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route('/show-weekly-shifts/<int:staff_id>')
def show_weekly_shifts(staff_id):
    today = get_today()
    start_of_week = today - timedelta(days=today.weekday())  # Monday
    end_of_week = start_of_week + timedelta(days=6)          # Sunday

    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            cur.execute("""
                SELECT date, start_time, end_time 
                FROM shifts 
                WHERE staff_id = %s AND date BETWEEN %s AND %s
                ORDER BY date
            """, (staff_id, start_of_week, end_of_week))
            shifts = cur.fetchall()

            cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
            staff = cur.fetchone()

            return render_template(
                'weekly_shifts.html',
                shifts=shifts,
                staff=staff,
                start=start_of_week,
                end=end_of_week,
                today=today
            )
    finally:
        connection.close()

@app.route('/delete-staff', methods=['POST'])
def delete_staff():
    staff_ids = request.form.getlist('staff_ids[]')

    if not staff_ids:
        return "❌ No staff selected."

    connection = get_db_connection()
    try:
        with connection.cursor() as cur:
            for sid in staff_ids:
                cur.execute("DELETE FROM shifts WHERE staff_id = %s", (sid,))
                cur.execute("DELETE FROM leaves WHERE staff_id = %s", (sid,))
                cur.execute("DELETE FROM staff WHERE id = %s", (sid,))

            connection.commit()
            return redirect(url_for('dashboard'))
    finally:
        connection.close()

@app.route("/")
def home():
    return "Flask is running successfully on Render!"

if __name__ == '__main__':  # fix here
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)