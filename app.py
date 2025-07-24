from flask import Flask, flash, render_template, request, redirect, url_for, send_file
from flask_mysqldb import MySQL
from datetime import datetime, time, timedelta
import MySQLdb.cursors
from urllib.parse import urlencode
from openpyxl import Workbook
from io import BytesIO
from collections import defaultdict
from dotenv import load_dotenv
import os

load_dotenv()
    
app = Flask(__name__)
app.config.from_pyfile('config.py')
app.secret_key = 'abcd1234'
mysql = MySQL(app)

# Utility functions
def is_night_shift(start_time_str: str, end_time_str: str) -> bool:
    try:
        st = datetime.strptime(start_time_str, "%H:%M:%S").time()
    except ValueError:
        st = datetime.strptime(start_time_str, "%H:%M").time()
    return st >= time(17, 0) or st < time(2, 0)

# Helper to convert timedelta to time
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
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT start_time, end_time FROM shifts
        WHERE staff_id = %s AND date = %s
    """, (staff_id, date))
    overlaps = []
    for os_, oe in cur.fetchall():
        # overlap if not (new_end <= os_ OR new_start >= oe)
        if not (new_end <= os_ or new_start >= oe):
            overlaps.append((os_, oe))
    return overlaps

# Routes
@app.route('/')
def dashboard():
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("SELECT id, name, position, total_leaves, total_night_shifts FROM staff")
    staff = cur.fetchall()
    today = datetime.today().date()
    shift_data = {}
    for s in staff:
        cur.execute("SELECT id, date, start_time, end_time FROM shifts WHERE staff_id=%s AND date=%s ORDER BY start_time", (s["id"], today))
        shift_data[s["id"]] = cur.fetchall()
    return render_template('dashboard.html', staff=staff, shift_data=shift_data)

@app.route('/assign-shift', methods=['GET', 'POST'])
def assign_shift():
    cur = mysql.connection.cursor()

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
                flash(f"⚠️ Invalid time format for shift on {date_str}.", "danger")
                continue

            start_dt = datetime.combine(date_obj, start_time)
            end_dt = datetime.combine(date_obj, end_time)

            # Overlap check
            cur.execute("""
                SELECT sh.start_time, sh.end_time
                FROM shifts sh
                WHERE sh.date = %s AND sh.staff_id = %s
            """, (date_str, staff_id))
            overlaps = []
            for existing_start, existing_end in cur.fetchall():
                existing_start = to_time(existing_start)
                existing_end = to_time(existing_end)
                existing_start_dt = datetime.combine(date_obj, existing_start)
                existing_end_dt = datetime.combine(date_obj, existing_end)
                if start_dt < existing_end_dt and end_dt > existing_start_dt:
                    overlaps.append(f"{existing_start.strftime('%H:%M')} - {existing_end.strftime('%H:%M')}")

            if overlaps:
                flash(f"❌ Time Overlapping with existing shift(s): {', '.join(overlaps)} on {date_str}", "danger")
                continue

            # Night shift logic
            is_night = is_night_shift(start_str, end_str)
            if is_night:
                cur.execute("""
                    SELECT COUNT(*) FROM shifts
                    WHERE staff_id = %s AND is_night_shift = 1 AND MONTH(date) = MONTH(CURDATE())
                """, (staff_id,))
                if cur.fetchone()[0] >= 8:
                    flash(f"⚠️ Night shift quota reached for staff {staff_id}.", "warning")
                    continue

            # Insert shift
            cur.execute("""
                INSERT INTO shifts (staff_id, date, start_time, end_time, is_night_shift)
                VALUES (%s, %s, %s, %s, %s)
            """, (staff_id, date_str, start_str, end_str, int(is_night)))

            if is_night:
                cur.execute("""
                    UPDATE staff SET total_night_shifts = total_night_shifts + 1 WHERE id = %s
                """, (staff_id,))

        mysql.connection.commit()
        return redirect(url_for('dashboard'))

    cur.execute("SELECT id, name FROM staff")
    staff = cur.fetchall()
    return render_template('assign_shift.html', staff=staff)


@app.route('/export_leaves', methods=['POST'])
def export_leaves():
    staff_id = request.form.get('staff_id')
    if not staff_id:
        return "No staff selected", 400
    cur = mysql.connection.cursor()
    cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
    row = cur.fetchone()
    if not row:
        return "Staff not found", 404
    staff_name = row[0]
    cur.execute("SELECT date FROM leaves WHERE staff_id = %s ORDER BY date DESC", (staff_id,))
    leave_dates = cur.fetchall()
    wb = Workbook()
    ws = wb.active
    ws.title = "Leave History"
    ws.append([f"Leave History for {staff_name}"])
    ws.append([])
    ws.append(['Date'])
    for ld in leave_dates:
        ws.append([ld[0].strftime("%Y-%m-%d")])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        download_name=f"{staff_name}_leave_history.xlsx",
        as_attachment=True
    )

@app.route('/export-shifts', methods=['POST'])
def export_shifts():
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    selected_ids = request.form.getlist('staff_ids')
    if not (start_date and end_date and selected_ids):
        return "Missing filters", 400
    cur = mysql.connection.cursor()
    fmt = ','.join(['%s'] * len(selected_ids))
    cur.execute(f"""
        SELECT s.name, sh.date, sh.start_time, sh.end_time,
               CASE WHEN sh.is_night_shift=1 THEN 'Yes' ELSE 'No' END
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
        ws.append(r)
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        download_name='shift_report.xlsx',
        as_attachment=True
    )

@app.route('/add-staff', methods=['GET', 'POST'])
def add_staff():
    if request.method == 'POST':
        name = request.form['name']
        position = request.form['position']
        cur = mysql.connection.cursor()
        cur.execute("INSERT INTO staff (name, position) VALUES (%s, %s)", (name, position))
        mysql.connection.commit()
        return redirect(url_for('dashboard'))
    return render_template('add_staff.html')



@app.route('/shifts-range', methods=['GET'])
def shifts_range():
    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
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
def staff_available(staff_id, date, start_time, end_time):
    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM shifts
        WHERE staff_id = %s AND date = %s
    """, (staff_id, date))
    count = cur.fetchone()[0]
    return count == 0

#view shifts
@app.route('/view-shift-form', methods=['GET', 'POST'])
def view_shift_form():
    cur = mysql.connection.cursor()

    cur.execute("SELECT id, name FROM staff")
    staff_list = cur.fetchall()

    shifts = []
    start_date = end_date = ''
    selected_ids = []

    if request.method == 'POST':
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        selected_ids = request.form.getlist('staff_ids')

    elif request.method == 'GET' and 'start_date' in request.args:
        start_date = request.args['start_date']
        end_date = request.args['end_date']
        selected_ids = request.args.getlist('staff_ids')

    if start_date and end_date and selected_ids:
        format_strings = ','.join(['%s'] * len(selected_ids))
        cur.execute(
            f"""
            SELECT s.name, sh.date, sh.start_time, sh.end_time, sh.staff_id, sh.id
            FROM shifts sh
            JOIN staff s ON s.id = sh.staff_id
            WHERE sh.date BETWEEN %s AND %s AND sh.staff_id IN ({format_strings})
            ORDER BY sh.date, s.name
            """,
            [start_date, end_date] + selected_ids
        )
        shifts = cur.fetchall()

    return render_template(
        'view_shift_form.html',
        staff_list=staff_list,
        shifts=shifts,
        start_date=start_date,
        end_date=end_date,
        selected_ids=selected_ids
    )




@app.route('/delete-shift/<int:shift_id>', methods=['POST'])
def delete_shift(shift_id):
    cur = mysql.connection.cursor()

    cur.execute("SELECT staff_id, is_night_shift FROM shifts WHERE id = %s", (shift_id,))
    result = cur.fetchone()
    if not result:
        flash("❌ Shift not found.", "error")
        return redirect(url_for('view_shift_form'))

    staff_id, is_night_shift = result

    if is_night_shift:
        cur.execute("""
            UPDATE staff
            SET total_night_shifts = GREATEST(total_night_shifts - 1, 0)
            WHERE id = %s
        """, (staff_id,))

    cur.execute("DELETE FROM shifts WHERE id = %s", (shift_id,))
    mysql.connection.commit()

    flash("✅ Shift deleted successfully.", "success")

    # Preserve filters on redirect
    start_date = request.form.get("start_date")
    end_date = request.form.get("end_date")
    staff_ids = request.form.getlist("staff_ids")

    params = {'start_date': start_date, 'end_date': end_date}
    for staff_id in staff_ids:
        params.setdefault('staff_ids', []).append(staff_id)

    query = urlencode(params, doseq=True)
    return redirect(f"/view-shift-form?{query}")





#replacement
@app.route('/replacement')
def replacement():
    date = request.args.get('date')
    start_time = request.args.get('start_time')
    end_time = request.args.get('end_time')

    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name FROM staff")
    all_staff = cur.fetchall()

    available = []
    for staff in all_staff:
        if staff_available(staff[0], date, start_time, end_time):
            available.append(staff)

    return render_template('replacement.html', available=available, date=date, start_time=start_time, end_time=end_time)

@app.route('/apply-leave/<int:staff_id>', methods=['POST'])
def apply_leave(staff_id):
    date = request.form['date']
    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT COUNT(*) FROM leaves
        WHERE staff_id = %s AND date = %s
    """, (staff_id, date))
    existing = cur.fetchone()[0]

    if existing > 0:
        return "❌ Leave already applied for this date."

    cur.execute("""
        SELECT COUNT(*) FROM leaves
        WHERE staff_id = %s AND MONTH(date) = MONTH(CURDATE()) AND YEAR(date) = YEAR(CURDATE())
    """, (staff_id,))
    leave_count = cur.fetchone()[0]

    if leave_count >= 4:
        return "❌ Leave limit (4/month) reached."

    cur.execute("INSERT INTO leaves (staff_id, date) VALUES (%s, %s)", (staff_id, date))
    cur.execute("UPDATE staff SET total_leaves = total_leaves + 1 WHERE id = %s", (staff_id,))

    mysql.connection.commit()
    return redirect(url_for('dashboard'))

@app.route('/apply-leave', methods=['GET', 'POST'])
def apply_leave_form():
    cur = mysql.connection.cursor()

    if request.method == 'POST':
        staff_id = request.form['staff_id']
        date = request.form['date']

        cur.execute("""
            SELECT COUNT(*) FROM leaves
            WHERE staff_id = %s AND MONTH(date) = MONTH(CURDATE())
        """, (staff_id,))
        leave_count = cur.fetchone()[0]

        if leave_count >= 4:
            return "❌ Leave limit (4/month) reached."

        cur.execute("INSERT INTO leaves (staff_id, date) VALUES (%s, %s)", (staff_id, date))
        cur.execute("UPDATE staff SET total_leaves = total_leaves + 1 WHERE id = %s", (staff_id,))
        mysql.connection.commit()

        return redirect(url_for('dashboard'))

    cur.execute("SELECT id, name FROM staff")
    staff = cur.fetchall()
    return render_template('apply_leave.html', staff=staff)

@app.route('/edit-position/<int:staff_id>', methods=['GET', 'POST'])
def edit_position(staff_id):
    cur = mysql.connection.cursor()

    if request.method == 'POST':
        new_position = request.form['position']
        cur.execute("UPDATE staff SET position = %s WHERE id = %s", (new_position, staff_id))
        mysql.connection.commit()
        return redirect(url_for('dashboard'))

    cur.execute("SELECT id, name, position FROM staff WHERE id = %s", (staff_id,))
    staff = cur.fetchone()
    return render_template('edit_position.html', staff=staff)

@app.route('/edit-shift/<int:shift_id>', methods=['GET', 'POST'])
def edit_shift(shift_id):
    cur = mysql.connection.cursor()

    if request.method == 'POST':
        start_time_raw = request.form['start_time']
        end_time_raw = request.form['end_time']
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        staff_ids = request.form.getlist('staff_ids')

        start_time = datetime.strptime(start_time_raw, "%H:%M").time()
        end_time = datetime.strptime(end_time_raw, "%H:%M").time()

        start_time_str = start_time.strftime("%H:%M:%S")
        end_time_str = end_time.strftime("%H:%M:%S")

        is_night = is_night_shift(start_time_str, end_time_str)

        cur.execute("""
            UPDATE shifts 
            SET start_time = %s, end_time = %s, is_night_shift = %s 
            WHERE id = %s
        """, (start_time_str, end_time_str, int(is_night), shift_id))

        mysql.connection.commit()

        params = {'start_date': start_date, 'end_date': end_date}
        for staff_id in staff_ids:
            params.setdefault('staff_ids', []).append(staff_id)

        return redirect(f"/view-shift-form?{urlencode(params, doseq=True)}")

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
@app.route('/show-leaves-form', methods=['GET', 'POST'])
def show_leaves_form():
    cur = mysql.connection.cursor()
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
            staff_name = row[0]

        cur.execute("""
            SELECT date FROM leaves
            WHERE staff_id = %s ORDER BY date DESC
        """, (selected_staff,))
        leave_records = cur.fetchall()

        # Monthly summary
        summary = defaultdict(int)
        for (leave_date,) in leave_records:
            month_year = leave_date.strftime("%B %Y")  # leave_date is already a date object
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



@app.route('/clear-shifts-today', methods=['POST'])
def clear_shifts_today():
    today = datetime.today().date()
    cur = mysql.connection.cursor()

    cur.execute("""
        SELECT staff_id, COUNT(*) FROM shifts
        WHERE date = %s AND is_night_shift = 1
        GROUP BY staff_id
    """, (today,))
    night_shift_counts = cur.fetchall()
    for staff_id, count in night_shift_counts:
        cur.execute("""
            UPDATE staff SET total_night_shifts = GREATEST(total_night_shifts - %s, 0)
            WHERE id = %s
        """, (count, staff_id))

    cur.execute("DELETE FROM shifts WHERE date = %s", (today,))
    mysql.connection.commit()

    return redirect(url_for('dashboard'))

@app.route('/clear-leaves', methods=['POST'])
def clear_leaves():
    cur = mysql.connection.cursor()
    cur.execute("UPDATE staff SET total_leaves = 0")
    mysql.connection.commit()
    return redirect(url_for('dashboard'))

@app.route('/clear-night-shifts', methods=['POST'])
def clear_night_shifts():
    cur = mysql.connection.cursor()
    cur.execute("UPDATE staff SET total_night_shifts = 0")
    mysql.connection.commit()
    return redirect(url_for('dashboard'))

@app.route('/clear-night-shifts/<int:staff_id>', methods=['POST'])
def clear_individual_night_shifts(staff_id):
    cur = mysql.connection.cursor()
    cur.execute("UPDATE staff SET total_night_shifts = 0 WHERE id = %s", (staff_id,))
    mysql.connection.commit()
    return redirect(url_for('dashboard'))

@app.route('/show-weekly-shifts/<int:staff_id>')
def show_weekly_shifts(staff_id):
    today = datetime.today().date()
    weekday = today.weekday()
    start_of_week = today if weekday == 0 else today - timedelta(days=weekday)
    end_of_week = start_of_week + timedelta(days=6)

    cur = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cur.execute("""
        SELECT date, start_time, end_time 
        FROM shifts 
        WHERE staff_id = %s AND date BETWEEN %s AND %s
        ORDER BY date
    """, (staff_id, start_of_week, end_of_week))
    shifts = cur.fetchall()

    cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
    staff = cur.fetchone()

    return render_template('weekly_shifts.html', shifts=shifts, staff=staff, start=start_of_week, end=end_of_week)

@app.route('/delete-staff', methods=['POST'])
def delete_staff():
    staff_ids = request.form.getlist('staff_ids[]')


    if not staff_ids:
        return "❌ No staff selected."

    cur = mysql.connection.cursor()

    for sid in staff_ids:
        cur.execute("DELETE FROM shifts WHERE staff_id = %s", (sid,))
        cur.execute("DELETE FROM leaves WHERE staff_id = %s", (sid,))
        cur.execute("DELETE FROM staff WHERE id = %s", (sid,))

    mysql.connection.commit()
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(debug=True)
