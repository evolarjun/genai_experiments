I want to create a viewer for tab-delimited files that operates similarly to less -S in that it cuts long lines, but allows you to scroll horizontally to view the full line in a text user interface.

The program should have the following characteristics

- The program should take a tab-delimited text file either on STDIN or as a command-line option. 
- The tab-delimited fields are changed to fixed width so each field value appears as a column.
- The first line of the text file is treated as a header and always shown no matter how many rows are in the file.
- Above the first line there appear column numbers starting at one.
- The user can scroll up and down and, if the line is longer than the viewport left and right using the arrow keys.
- The program should be written in perl.
- Scrolling horizontally will redraw the whole screen shifted by 10 characters in the corresponding direction.

