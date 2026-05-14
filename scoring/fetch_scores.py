#!/usr/bin/env python3
"""
Fetch live Masters scores from ESPN and compute pool standings.
Outputs pool_scores.json matching the StandingsResponse format the dashboard expects.
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
import sys
import os
import re

ESPN_URL = "https://site.web.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"

# Augusta National pars (fixed)
AUGUSTA_PARS = [4, 5, 4, 3, 4, 3, 4, 5, 4, 4, 4, 3, 5, 4, 5, 3, 4, 4]

# ── Teams ─────────────────────────────────────────────────────
TEAMS = {
    'Pat':          ['Scottie Scheffler', 'Jordan Spieth', 'Adam Scott', 'Jacob Bridgeman', 'Ben Griffin', 'Wyndham Clark'],
    'Trizz':        ['Rory McIlroy', 'Si Woo Kim', 'Shane Lowry', 'Sungjae Im', 'Min Woo Lee', 'Marco Penge'],
    'Kelly':        ['Cameron Young', 'Patrick Cantlay', 'Chris Gotterup', 'Gary Woodland', 'Sam Burns', 'Harry Hall'],
    'Berit':        ['Bryson DeChambeau', 'Russell Henley', 'Viktor Hovland', 'Harris English', 'Kristoffer Reitan', 'Sudarshan Yellamaraju'],
    'Justin (Doc)': ['Matt Fitzpatrick', 'Patrick Reed', 'Rickie Fowler', 'J.J. Spaun', 'Alex Fitzpatrick', 'Matt McCarty'],
    'Wheats':       ['Tommy Fleetwood', 'Justin Thomas', 'Denny McCarthy', 'Brian Harman', 'Alex Noren', 'Daniel Berger'],
    'JB':           ['Xander Schauffele', 'Tyrrell Hatton', 'Robert MacIntyre', 'Joaquin Niemann', 'Thomas Detry', 'Nick Taylor'],
    'Nick':         ['Ludvig Aberg', 'Hideki Matsuyama', 'Corey Conners', 'Sepp Straka', 'Keegan Bradley', 'Sahith Theegala'],
    'Ian':          ['Jon Rahm', 'Brooks Koepka', 'Jason Day', 'Akshay Bhatia', 'Nicolai Hojgaard', 'Rasmus Hojgaard'],
    'Ben':          ['Collin Morikawa', 'Justin Rose', 'Maverick McNealy', 'Kurt Kitayama', 'Ryan Gerard', 'Michael Thorbjornsen'],

