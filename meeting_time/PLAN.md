World time meeting planner

Create a plan to build this app with an LLM. It would have a week calendar view with days represented with 24 hours so you could select a time zone or city and a set of hours for a meeting by clicking and dragging from the start time to the end time in local time and it would report the start and end time in the destination timezone. 

Details:

- The default (from) time zone should default to local time zone on page load.
- The to timezone can be set from a dropdown of major cities and timezone abbreviations like GMT.
- The week view should start on Sunday.
- The user will be able to select the time in hour increments. Hours could be buttons (for 1 hour meetings) so selecting 3pm would report the times of a meeting from 3-4pm.
- There is no functionality to handle dragging over midnight. Meetings need to occur all in one day
- I would prefer a single file HTML/JS solution.

# Prompt:

Role: Act as an expert Front-End Developer.

Task: Create a single-file HTML/JS "World Meeting Planner" app. The app allows a user to pick a time slot in their local timezone and see what time that corresponds to in a destination timezone.

Requirements & Features:

1. Tech Stack: Use a single HTML file. Include the Luxon library via CDN for timezone math. Use modern CSS (Flexbox/Grid) for the layout.

2. Timezone Setup:
   - "From" Timezone: Default to the user’s local timezone on page load.
   - "To" Timezone: Provide a dropdown menu containing major global cities (e.g., London, Tokyo, New York, New Delhi, Copenhagen, etc.) and common abbreviations like GMT, UTC, and CET.

3. The Calendar Grid:
   - Display a Week View starting on Sunday.
   - The grid should have 7 columns (days) and 24 rows (representing hours 0-23).
   - Each hour slot should be an interactive button.

4. Interaction Logic:
   - When a user clicks an hour button, it should represent a 1-hour meeting (e.g., clicking the "3 PM" button represents a 3:00 PM to 4:00 PM slot).
   - Highlight the selected button.
   - Constraint: Meetings must occur within a single day; do not allow selections that cross midnight.

5. Reporting:
   - Below or beside the grid, display a "Result Panel."
   - When a slot is clicked, report the start and end time in the "From" timezone AND the converted start and end time in the "To" timezone.
   - Example: "Your Time (New York): 3:00 PM - 4:00 PM | Their Time (London): 8:00 PM - 9:00 PM."

6. UI/UX:
   - Use a clean, professional design.
   - Clearly label the dates and days of the week.
   - Include a set of arrows pointing left and right to change the week.
   - Use a 12 hour format and list the hours with a number and am or pm like 8am 9am 10am.
   - The result should be in an easy to read format like Friday March 27 from 3pm to 4pm

