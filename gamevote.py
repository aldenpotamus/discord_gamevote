import pygsheets
import discord
import configparser
import sys
import re
import json
import random
from datetime import datetime
import bitlyshortener
import time
import string
import json

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

messageTemplate = string.Template('${game} - ${short_link} ${votesVisual}')
sheetTitleTemplate = string.Template('backup ${sheetNumber} [${date}]')
sheetTitleParser = re.compile('([^\s]*)\s([0-9]*)\s\[([^]]*)\]')

discordIntents = discord.Intents.default()
discordIntents.members = True
discordIntents.presences = True
discordIntents.message_content = True
discordClient = discord.Client(intents=discordIntents)

@discordClient.event
async def on_ready():
    channel = await discordClient.fetch_channel(CONFIG['DISCORD']['discordChannelId'])

    # Read Existing Spreadsheet
    print('Read Existing Spreadsheet')
    games, voters = await readFromSheet()

    # Build Backup Sheet
    print('Build Backup Sheet')
    await backupVoteSheet()
    await cleanupOldBackups()
    
    # Scan for New Games
    print('Scan for New Games')
    await readGamesFromDiscord(games, voters, channel)
    
    # Migrate manual approved games over to new sheet
    print('Migrating approvals from manual review worksheet...')
    await migrateApprovals(games, voters)
    
    # Build Spreadsheet From Games List
    print('Build Spreadsheet From Games List')
    await writeToSheet(games)
    
    # Write Message From Spreadsheet
    print('Write Message From Spreadsheet')
    await writeGamesToDiscord(games, channel)
    
    print('Done')

    await discordClient.close()

async def shortenUrl(longUrl):
    # print('Bitly Magic')
    return bitly.shorten_urls([longUrl])[0]

async def readFromSheet():
    voters = {}
    
    games = {}
    for row in votesWorksheet.get_values(start='A3', end='J', returnas='matrix')[2:]:
        dictRow = { x.lower(): y for (x,y) in zip(header, row)}
        games[dictRow['short_link']] = dictRow
        games[dictRow['short_link']]["votes"] = json.loads(games[dictRow['short_link']]["votes"])

        for key in games[dictRow['short_link']]["votes"].keys():
            for voter in games[dictRow['short_link']]["votes"][key]:
                if voter not in voters.keys():
                    voters[voter] = []
                voters[voter].append((key, dictRow['short_link']))

    return games, voters

async def writeToSheet(games):
    rowsToWrite = [ {'total_votes': -1000 if (games[game]['vetoed'] == "TRUE" or games[game]['played'] == "TRUE") else games[game]['total_votes'], 'gameData': games[game]} for game in games.keys() ]
    rowsToWrite.sort(key=lambda x: int(x['total_votes']), reverse=True)

    for gameData in [row['gameData'] for row in rowsToWrite]:
        gameData['votes'] =  json.dumps(gameData['votes'], ensure_ascii=False)
        gameData['rowData'] = [gameData[value] if value in gameData else None for value in header]
        # print(str([gameData[value] if value in gameData else None for value in header]).encode('utf-8'))
    
    rows = len(games.keys())
    # print(str(rowsToWrite).encode('utf-8'))

    votesWorksheet.insert_rows(2, number=rows, values=[row['gameData']['rowData'] for row in rowsToWrite], inherit=True)
    votesWorksheet.set_data_validation(start='A3', end='B'+str(3+rows), condition_type='BOOLEAN')
    

async def readGamesFromDiscord(games, voters, channel):
    print('Getting message history and updating reactions...')

    async for message in channel.history():
        author = message.author
        messageText = message.content.replace("```", "")
        reactions = message.reactions

        if author.name+'#'+author.discriminator != CONFIG['DISCORD']['botId']:
            print('Processing: '+str(messageText.encode("ascii", "ignore")))
            urlMatch = re.compile("(?P<url>https?://[^\s]+)")
            urlBaseMatch = re.compile('^(?P<base>https?:\/\/[^\/]+)\/*.*$')

            if urlMatch.search(messageText):
                url = urlMatch.search(messageText).group("url")
                urlBase = urlBaseMatch.search(url).group("base")

                url = cleanUrl(url)
                urlBase = cleanUrl(urlBase)

                print("url: " + url)
                print("base: " + urlBase)
            else:
                print('Chat message, no URL present')
                continue

            if not urlMatch:
                #Regular message moving on
                print("Message contained no URL skipping!")
            elif not urlBase in gamesiteWhitelist.keys():
                # Non whitelisted gamesite
                print('Manual review needed, link isn\'t to steam.')
                # preSubmitReactions = { reaction.emoji if isinstance(reaction.emoji, str) else "🚀" : [author.name+'#'+author.discriminator for author in reaction.users()] for reaction in reactions }
                preSubmitReactions = {}
                for reaction in reactions:
                    async for user in reaction.users():
                        emote = reaction.emoji if isinstance(reaction.emoji, str) else "🚀"
                        if emote not in preSubmitReactions.keys():
                            preSubmitReactions[emote] = []
                        preSubmitReactions[emote].append(user.name+'#'+user.discriminator)
                    manualReviewWorksheet.insert_rows(2, number=1, values=[False, False, url, author.name+'#'+author.discriminator, json.dumps(preSubmitReactions, ensure_ascii=False)], inherit=True)
                continue
            else:
                # Check if a game is submitted that was already in the game list
                gameAlreadySubmitted = False
                for game in games.values():
                    if game["link"] == url:
                        print("Game link already in the game list, adding vote instead...")
                        if game['suggester'] != author.name+'#'+author.discriminator:
                            game['voters'] += ','+author.name+'#'+author.discriminator                    
                            await addVoteToGame(games,
                                                voters,
                                                "😳",
                                                author.name+'#'+author.discriminator ,
                                                game['short_link'])                        
                        else:
                            print("Game suggester resubmitted... ignoring.")
                        gameAlreadySubmitted = True

                if not gameAlreadySubmitted:
                    # New Submission w/ a whitelisted game listing
                    print('Creating new game entry...')
                    gameKey = gamesiteWhitelist[urlBase].search(url).group("game")
                    gameName = string.capwords(re.sub(r'[_-]', " ", gameKey))[0:25]
                    shortLink = await shortenUrl(url)
                    suggester = author.name+'#'+author.discriminator

                    games[shortLink] = {
                        'vetoed': 'FALSE',
                        'played': 'FALSE',
                        'date': dateString,
                        'game': gameName,
                        'link': url,
                        'short_link': shortLink,
                        'total_votes': 0,
                        'suggester': suggester,
                        'voters':  '',
                        'votes': {}
                    }

                    for reaction in reactions:
                        async for user in reaction.users():
                            await addVoteToGame(games,
                                                voters,
                                                reaction.emoji if isinstance(reaction.emoji, str) else "🚀",
                                                user.name+'#'+user.discriminator,
                                                shortLink)

        else:
            #Existing game, update votes
            users = {}
            shortLink = messageText.split(' - ')[1].split(' ')[0]

            if shortLink in games.keys():
                for reaction in reactions:
                    async for user in reaction.users():
                        await addVoteToGame(games,
                                            voters,
                                            reaction.emoji if isinstance(reaction.emoji, str) else "🚀",
                                            user.name+'#'+user.discriminator,
                                            shortLink)
            else:
                print('Game ['+shortLink+'] not present in sheet, assuming deletion.')
            
    return

async def addVoteToGame(games, voters, emote, user, shortLink):
    print('Adding vote.')

    if user in voters:
        if shortLink in [x[1] for x in voters[user]]:
            print('User already voted for this game, updating vote emoji.')
            # Delete old vote from games
            oldEmote = None
            for key in games[shortLink]['votes'].keys():
                if user in games[shortLink]['votes'][key]:
                    games[shortLink]['votes'][key].remove(user)
                    oldEmote = key

            # Remove old voter from voters
            voters[user].remove((oldEmote, shortLink))

    # Add new vote to games
    if emote not in games[shortLink]['votes'].keys():
        games[shortLink]['votes'][emote] = []
    games[shortLink]['votes'][emote].append(user)

    # Update counts
    games[shortLink]['voters'] = ",".join([item for sublist in [x for x in games[shortLink]['votes'].values()] for item in sublist])
    games[shortLink]['total_votes'] = sum([ len(v) for v in games[shortLink]['votes'].values() ])

    # Update voters
    if user not in voters:
        voters[user] = []
    voters[user].append((emote, shortLink))

    return

async def writeGamesToDiscord(games, channel):
    await discordClearChannel(channel)
 
    for key, gameData in games.items():
        votesDict = json.loads(gameData["votes"])
        gameData["votesVisual"] = "".join([x*len(votesDict[x]) for x in votesDict.keys()])

    messagesToSend = [ {'total_votes': game['total_votes'], 'messageText': messageTemplate.safe_substitute(game)} for
                        game in games.values() if game['vetoed'] == 'FALSE' and game['played'] == 'FALSE' ]
    messagesToSend.sort(key=lambda x: int(x['total_votes']), reverse=False)

    for message in messagesToSend:
        message = await channel.send(message['messageText'])
        # await message.edit(suppress=True)
    
    await channel.send(CONFIG['GENERAL']['instructions'])

async def discordClearChannel(channel):
    messages = []
    async for message in channel.history(limit=100):
        messages.append(message)
    await channel.delete_messages(messages)

async def backupVoteSheet():
    global votesWorksheet
    backupName = await checkAndIncrementBackupSheet(votesWorksheet)

    votesWorksheet.title = backupName
    votesWorksheet = sheet.add_worksheet(CONFIG['SHEET']['mainSheet'], src_worksheet=templateWorksheet)
    
    # Shuffle games sheets so backups are at the end.
    votesWorksheet.index = 0
    templateWorksheet.index = 1
    manualReviewWorksheet.index = 2
    configWorksheet.index = 3

async def checkAndIncrementBackupSheet(targetSheet):
    if sheetTitleParser.match(targetSheet.title) == None:
        desiredBackupName = sheetTitleTemplate.safe_substitute({ 'sheetNumber': str(1), 'date': dateString })  
    else:
        number = int(sheetTitleParser.match(targetSheet.title).group(2))
        desiredBackupName = sheetTitleTemplate.safe_substitute({ 'sheetNumber': str(number + 1), 'date': dateString })  

    try:
        destinationSheet = sheet.worksheet_by_title(desiredBackupName)
    except pygsheets.exceptions.WorksheetNotFound: 
        return desiredBackupName
    
    availableName = await checkAndIncrementBackupSheet(destinationSheet)
    print(availableName)
    destinationSheet.title = availableName

    return desiredBackupName

async def cleanupOldBackups():
    allSheets = sheet.worksheets()
    for s in allSheets:
        groups = sheetTitleParser.match(s.title)
        if sheetTitleParser.match(s.title):
            backupDate = datetime.fromisoformat(groups.group(3))
            if (datetime.now()-backupDate).days > int(CONFIG['GENERAL']['maxBackupAgeDays']):
                print('Removing sheet named: '+s.title)
                sheet.del_worksheet(s)

async def migrateApprovals(games, voters):
    manualRows = manualReviewWorksheet.get_values(start='A3', end='E50', returnas='matrix')

    for rowNum, row in enumerate(manualRows):
        url = row[2]
        suggester = row[3]
        votes = json.loads(row[4])

        urlBaseMatch = re.compile('^(?P<base>https?:\/\/[^\/]+)\/*.*$')
        urlBase = urlBaseMatch.search(url).group("base")

        urlBase = cleanUrl(urlBase)       

        if not row[1] == 'TRUE' and row[0] == "TRUE":
            print('Game approved for inclusion... starting migration.')
            gameKey = gamesiteWhitelist[urlBase].search(url).group("game")
            gameName = string.capwords(re.sub(r'[_-]', " ", gameKey))[0:25]
            shortLink = await shortenUrl(url)

            games[shortLink] = {
                'vetoed': 'FALSE',
                'played': 'FALSE',
                'date': dateString,
                'game': gameName,
                'link': url,
                'short_link': shortLink,
                'total_votes': -1,
                'suggester': suggester,
                'voters':  '',
                'votes': {}
            }

            for emote in votes.keys():
                for voter in votes[emote]:
                    await addVoteToGame(games,
                                        voters,
                                        emote,
                                        voter,
                                        shortLink)
            
            manualReviewWorksheet.update_value("B"+str(rowNum+3), True)

    return

def cleanUrl (url):
    chrome_driver.get(url)
    return chrome_driver.current_url

def main():
    global bitly, dateString, header, sheet, votesWorksheet, templateWorksheet, manualReviewWorksheet, configWorksheet, gamesiteWhitelist, chrome_driver
    bitlyTokenPool = [ CONFIG['AUTHENTICATION']['bitlyToken'] ]
    bitly = bitlyshortener.Shortener(tokens=bitlyTokenPool, max_cache_size=256)

    print('Starting headless chrome driver...')
    chrome_options = Options()
    chrome_options = webdriver.ChromeOptions()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_driver = webdriver.Chrome(options=chrome_options)
    chrome_driver.implicitly_wait(10)

    dateString = datetime.now().strftime("%Y-%m-%d")
    
    gc = pygsheets.authorize(service_file=CONFIG['AUTHENTICATION']['serviceToken'])
    sheet = gc.open_by_key(CONFIG['SHEET']['id'])
    votesWorksheet = sheet.worksheet_by_title(CONFIG['SHEET']['mainSheet'])
    templateWorksheet = sheet.worksheet_by_title(CONFIG['SHEET']['templateSheet'])
    manualReviewWorksheet = sheet.worksheet_by_title(CONFIG['SHEET']['manualSheet'])
    configWorksheet = sheet.worksheet_by_title(CONFIG['SHEET']['configSheet'])

    header = votesWorksheet.get_values(start='A2', end='J2', returnas='matrix')[0]

    whitelist = configWorksheet.get_values(start='A2', end='B50', returnas='matrix')
    gamesiteWhitelist = { x[0]: re.compile(x[1]) for x in whitelist if x[0] != '' }

    discordClient.run(CONFIG['AUTHENTICATION']['discordToken'])

if __name__ == '__main__':
    print('Parsing config file...')
    CONFIG = configparser.ConfigParser()
    CONFIG.read('config.ini')

    main()