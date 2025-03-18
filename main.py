import os
import datetime
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle

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

        print("\nBusy periods found:")
        for email, calendar_data in calendars_data.items():
            busy_times = calendar_data.get('busy', [])


            busy_times_all.extend(busy_times)

        if not busy_times_all:
            print("\nNo busy periods found for any attendee.")

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

    def classify_attendees(self, attendees, internal_domain="mycompany.com"):
        internal = []
        external = []

        for email in attendees:
            if email.lower().endswith(f"@{internal_domain}"):
                internal.append(email)
            else:
                external.append(email)

        return {
            'internal': internal,
            'external': external
        }

def get_validated_input(prompt, validation_func=None, error_message=None):
    while True:
        user_input = input(prompt)
        if validation_func is None or validation_func(user_input):
            return user_input
        print(error_message or "Invalid input. Please try again.")

def get_multiple_emails():
    emails = []
    print("Enter attendee email addresses (enter 'done' when finished):")
    while True:
        email = input("Email address (or 'done'): ").strip()
        if email.lower() == 'done':
            break
        if '@' in email and '.' in email:  
            emails.append(email)
        else:
            print("Invalid email format. Please try again.")
    return emails

def parse_date(date_str):
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None

def get_date_range():
    print("\nSpecify date range to look for meeting slots:")


    start_date_str = get_validated_input(
        "Start date (YYYY-MM-DD) or press Enter for today: ",
        lambda x: not x or parse_date(x) is not None,
        "Invalid date format. Use YYYY-MM-DD."
    )

    if start_date_str:
        start_date = parse_date(start_date_str)
    else:
        start_date = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)   

        days_ahead_str = get_validated_input(
        "Number of days to look ahead (default is 5): ",
        lambda x: not x or x.isdigit(),
        "Please enter a valid number."
    )

    days_ahead = int(days_ahead_str) if days_ahead_str else 5

    return start_date, days_ahead

def get_timezone():
    default_timezone = "Asia/Kolkata"    

    print("\nCommon timezones: Asia/Kolkata, America/New_York, America/Chicago, America/Denver, America/Los_Angeles,")
    print("Europe/London, Europe/Paris, Asia/Tokyo, Asia/Shanghai, Australia/Sydney")    

    while True:
        timezone = input(f"Enter timezone (press Enter for {default_timezone}): ").strip()
        if not timezone:
            return default_timezone
        try:
            pytz.timezone(timezone)
            return timezone
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"Unknown timezone: {timezone}. Please try again.")

def get_meeting_details():
    title = get_validated_input("Meeting title: ", lambda x: len(x) > 0, "Title cannot be empty.")

    location = input("Meeting location (leave blank for virtual): ").strip()

    description = input("Meeting description: ").strip()

    duration_str = get_validated_input(
        "Meeting duration in minutes (default is 60): ",
        lambda x: not x or x.isdigit(),
        "Please enter a valid number."
    )
    duration = int(duration_str) if duration_str else 60

    return title, location, description, duration

def main():

    credentials_file = input("Enter path to credentials.json file (or press Enter for default 'credentials.json'): ").strip()
    if not credentials_file:
        credentials_file = 'credentials.json'

    if not os.path.exists(credentials_file):
        print(f"Error: Credentials file '{credentials_file}' not found.")
        print("Please make sure you've downloaded your OAuth credentials from the Google Cloud Console.")
        return

    try:
        organizer = MeetingOrganizer(credentials_path=credentials_file)
        organizer.authenticate()
        print("Authentication successful!")
    except Exception as e:
        print(f"Authentication failed: {e}")
        return


    print("\n=== Meeting Details ===")
    title, location, description, duration = get_meeting_details()   

    print("\n=== Attendees ===")
    attendees = get_multiple_emails()
    if not attendees:
        print("No attendees specified. At least one attendee is required.")
        return    

    start_date, days_ahead = get_date_range()    

    print("\n=== Timezone ===")
    timezone = get_timezone()    

    print("\n=== Work Hours ===")
    work_start_str = get_validated_input(
        "Work hours start (0-23, default is 9): ",
        lambda x: not x or (x.isdigit() and 0 <= int(x) <= 23),
        "Please enter a valid hour (0-23)."
    )
    work_start = int(work_start_str) if work_start_str else 9

    work_end_str = get_validated_input(
        "Work hours end (0-23, default is 17): ",
        lambda x: not x or (x.isdigit() and 0 <= int(x) <= 23),
        "Please enter a valid hour (0-23)."
    )
    work_end = int(work_end_str) if work_end_str else 18

    print(f"\nSearching for available meeting times (timezone: {timezone})...")
    available_slots = organizer.find_free_time(
        attendees=attendees,
        duration_minutes=duration,
        start_date=start_date,
        end_date=start_date + datetime.timedelta(days=days_ahead),
        work_hours=(work_start, work_end),
        timezone=timezone
    )

    if not available_slots:
        print("No available time slots found for all attendees in the specified date range.")
        return    

    print(f"\nFound {len(available_slots)} available time slots (in {timezone}):")
    for i, slot in enumerate(available_slots):
        start = datetime.datetime.fromisoformat(slot['start'])
        end = datetime.datetime.fromisoformat(slot['end'])
        print(f"{i+1}. {start.strftime('%Y-%m-%d %H:%M')} - {end.strftime('%H:%M')}")    

    choice_str = get_validated_input(
        "\nSelect a slot by number (or 0 to cancel): ",
        lambda x: x.isdigit() and 0 <= int(x) <= len(available_slots),
        f"Please enter a number between 0 and {len(available_slots)}."
    )

    choice = int(choice_str)
    if choice == 0:
        print("Meeting creation cancelled.")
        return

    selected_slot = available_slots[choice-1]   

    print("\n=== Meeting Summary ===")
    print(f"Title: {title}")
    print(f"Time: {datetime.datetime.fromisoformat(selected_slot['start']).strftime('%Y-%m-%d %H:%M')} - "
          f"{datetime.datetime.fromisoformat(selected_slot['end']).strftime('%H:%M')} ({timezone})")
    print(f"Duration: {duration} minutes")
    print(f"Attendees: {', '.join(attendees)}")

    confirm = input("\nCreate this meeting? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Meeting creation cancelled.")
        return   

    try:
        meeting = organizer.create_meeting(
            summary=title,
            location=location,
            description=description,
            start_time=selected_slot['start'],
            end_time=selected_slot['end'],
            attendees=attendees,
            conference_data=True,
            timezone=timezone
        )        
        print("\nMeeting created successfully!")
        print(f"Calendar link: {meeting.get('htmlLink')}")
        if 'hangoutLink' in meeting:
            print(f"Google Meet link: {meeting.get('hangoutLink')}")
    except Exception as e:
        print(f"Error creating meeting: {e}")

if __name__ == "__main__":
    main()