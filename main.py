import os
import datetime
import pytz
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
from flask import Flask, request, jsonify

app = Flask(__name__)

class MeetingOrganizer:
    def __init__(self, credentials_path=None, token_path='token.pickle'):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        self.SCOPES = ['https://www.googleapis.com/auth/calendar']

    def authenticate(self):
        creds = None
        if os.path.exists(self.token_path):
            with open(self.token_path, 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, self.SCOPES)
                creds = flow.run_local_server(port=0)

            with open(self.token_path, 'wb') as token:
                pickle.dump(creds, token)

        self.service = build('calendar', 'v3', credentials=creds)
        print("Authenticated with OAuth")

    def find_free_time(self, attendees, duration_minutes=60, start_date=None, end_date=None, 
               work_hours=(9, 17), days_range=5, calendar_id='primary', timezone='UTC'):
        if not self.service:
            raise Exception("Not authenticated. Call authenticate() first")

        if not start_date:
            start_date = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if not end_date:
            end_date = start_date + datetime.timedelta(days=days_range)

        tz = pytz.timezone(timezone)
        if start_date.tzinfo is None:
            start_date = tz.localize(start_date)
        if end_date.tzinfo is None:
            end_date = tz.localize(end_date)

        time_min = start_date.astimezone(pytz.UTC).isoformat()
        time_max = end_date.astimezone(pytz.UTC).isoformat()

        items = [{"id": email} for email in attendees]

        current_user_email = self.service.calendars().get(calendarId='primary').execute().get('id')
        if current_user_email and current_user_email not in attendees:
            items.append({"id": "primary"})

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "timeZone": timezone,
            "items": items
        }

        freebusy_response = self.service.freebusy().query(body=body).execute()

        busy_times_all = []
        calendars_data = freebusy_response.get('calendars', {})

        for email, calendar_data in calendars_data.items():
            busy_times = calendar_data.get('busy', [])
            busy_times_all.extend(busy_times)

        time_slots = []
        current_time = start_date        
        time_increment = datetime.timedelta(minutes=30)        
        while current_time < end_date:
            if (current_time.weekday() < 5 and  
                work_hours[0] <= current_time.hour < work_hours[1]):

                slot_end = current_time + datetime.timedelta(minutes=duration_minutes)
                is_free = True

                for busy in busy_times_all:
                    busy_start = datetime.datetime.fromisoformat(busy['start'].replace('Z', '+00:00'))
                    busy_end = datetime.datetime.fromisoformat(busy['end'].replace('Z', '+00:00'))

                    busy_start = busy_start.astimezone(tz)
                    busy_end = busy_end.astimezone(tz)

                    if (current_time < busy_end and slot_end > busy_start):
                        is_free = False
                        break

                if is_free:
                    time_slots.append({
                        'start': current_time.isoformat(),
                        'end': slot_end.isoformat()
                    })

            current_time += time_increment

        return time_slots

    def create_meeting(self, summary, location, description, start_time, end_time, 
                      attendees, calendar_id='primary', conference_data=True, timezone='UTC'):
        if not self.service:
            raise Exception("Not authenticated. Call authenticate() first")            

        if isinstance(start_time, datetime.datetime):
            start_time = start_time.isoformat()
        if isinstance(end_time, datetime.datetime):
            end_time = end_time.isoformat()            

        attendee_list = [{'email': email} for email in attendees]       

        event_body = {
            'summary': summary,
            'location': location,
            'description': description,
            'start': {
                'dateTime': start_time,
                'timeZone': timezone,
            },
            'end': {
                'dateTime': end_time,
                'timeZone': timezone,
            },
            'attendees': attendee_list,
            'reminders': {
                'useDefault': True
            }
        }        

        if conference_data:
            event_body['conferenceData'] = {
                'createRequest': {
                    'requestId': f'meeting-{datetime.datetime.now().timestamp()}'
                }
            }           

        event = self.service.events().insert(
            calendarId=calendar_id,
            body=event_body,
            conferenceDataVersion=1 if conference_data else 0,
            sendUpdates='all'  
        ).execute()

        return event

@app.route('/create-meeting', methods=['POST'])
def create_meeting():
    data = request.json
    credentials_file = data.get('credentials_path', 'credentials.json')
    
    if not os.path.exists(credentials_file):
        return jsonify({"error": f"Credentials file '{credentials_file}' not found."}), 400

    try:
        organizer = MeetingOrganizer(credentials_path=credentials_file)
        organizer.authenticate()
    except Exception as e:
        return jsonify({"error": f"Authentication failed: {str(e)}"}), 500

    try:
        available_slots = organizer.find_free_time(
            attendees=data['attendees'],
            duration_minutes=data.get('duration', 60),
            start_date=datetime.datetime.fromisoformat(data['start_date']),
            end_date=datetime.datetime.fromisoformat(data['end_date']),
            work_hours=(data.get('work_start', 9), data.get('work_end', 17)),
            timezone=data.get('timezone', 'UTC')
        )
        
        if not available_slots:
            return jsonify({"error": "No available time slots found."}), 400

        selected_slot = available_slots[0]  # Automatically select the first available slot

        meeting = organizer.create_meeting(
            summary=data['summary'],
            location=data.get('location', ''),
            description=data.get('description', ''),
            start_time=selected_slot['start'],
            end_time=selected_slot['end'],
            attendees=data['attendees'],
            conference_data=True,
            timezone=data.get('timezone', 'UTC')
        )

        return jsonify({
            "calendar_link": meeting.get('htmlLink'),
            "google_meet_link": meeting.get('hangoutLink', '')
        })
    except Exception as e:
        return jsonify({"error": f"Error creating meeting: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True)